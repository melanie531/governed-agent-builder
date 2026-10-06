"""Receipt-bound, member-scoped update of existing Studio Business and Worker Lambdas."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import time
from zipfile import ZipFile

from botocore.config import Config
from botocore.exceptions import ClientError

from scripts.deployment_target import DeploymentTarget, target_arguments
from scripts.journey_foundation_release import multipart_helper
from scripts.mcp_onboarding_audit import (
    verified_scoped_consent_query_release, verified_scoped_deploy_only_release,
    verified_scoped_connected_popup_release,
    verified_scoped_reauth_release,
    verified_scoped_reauth_idle_release,
    verified_scoped_native_gateway_release,
    verified_scoped_callback_resume_release,
)

CHANGED = ["backend/journey.py", "backend/journey_cloud.py", "backend/journey_schema.py"]
CONNECTED_POPUP_CHANGED = ["backend/journey.py", "backend/mcp_user_connections.py"]
REAUTH_CHANGED = ["backend/journey.py", "backend/mcp_user_connections.py", "backend/mcp_user_oauth.py"]
REAUTH_IDLE_CHANGED = ["backend/mcp_user_connections.py"]
NATIVE_GATEWAY_CHANGED = [
    "backend/journey.py", "backend/journey_cloud.py",
    "backend/mcp_user_connections.py", "backend/mcp_user_oauth.py"]
CALLBACK_RESUME_CHANGED = ["backend/app.py", "backend/journey.py", "backend/mcp_user_connections.py"]
SESSION_AUTH_CHANGED = ["backend/journey.py", "backend/mcp_user_connections.py"]
RELEASES = {
    "consent-query": (CHANGED, verified_scoped_deploy_only_release),
    "connected-popup": (CONNECTED_POPUP_CHANGED, verified_scoped_consent_query_release),
    "reauth": (REAUTH_CHANGED, verified_scoped_connected_popup_release),
    "reauth-idle": (REAUTH_IDLE_CHANGED, verified_scoped_reauth_release),
    "native-gateway": (NATIVE_GATEWAY_CHANGED, verified_scoped_reauth_idle_release),
    "callback-resume": (CALLBACK_RESUME_CHANGED, verified_scoped_native_gateway_release),
    "session-auth": (SESSION_AUTH_CHANGED, verified_scoped_callback_resume_release),
}


def _sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _lambda_sha(digest):
    return base64.b64encode(bytes.fromhex(digest)).decode()


def package_changes(base, patch, base_sha, patch_sha, expected=CHANGED):
    if _sha(base) != base_sha or _sha(patch) != patch_sha:
        raise ValueError("Reviewed package digest differs")
    with ZipFile(base) as original, ZipFile(patch) as updated:
        names = original.namelist()
        if (names != updated.namelist() or len(names) != len(set(names))
                or original.testzip() or updated.testzip()):
            raise ValueError("Reviewed ZIP member inventory is invalid")
        changed = sorted(name for name in names if original.read(name) != updated.read(name))
        if changed != expected:
            raise ValueError("Reviewed ZIP changed members differ")
        return changed


def reconcile_code(intent, live, base_sha, patch_sha):
    before = intent["revision_before"]
    if (live["RevisionId"] != before and live["CodeSha256"] == patch_sha
            and live["State"] == "Active" and live["LastUpdateStatus"] == "Successful"):
        if intent.get("phase") == "VERIFIED" and live["RevisionId"] != intent.get("revision_after"):
            raise ValueError("Lambda changed outside the verified release")
        return "verified"
    if live["RevisionId"] == before and live["CodeSha256"] == base_sha:
        return "uncertain"
    if live["CodeSha256"] in (base_sha, patch_sha) and live.get("LastUpdateStatus") == "InProgress":
        return "pending"
    raise ValueError("Lambda changed outside the retained release intent")


class ScopedRelease:
    def __init__(self, args):
        self.kind = args.release_kind
        self.changed, predecessor = RELEASES[self.kind]
        self.target = DeploymentTarget(args.expected_account, args.profile, args.region, args.state)
        self.path = args.receipt.resolve()
        self.package = args.package.resolve()
        self.bases = {"Business": args.business_base.resolve(), "Worker": args.worker_base.resolve()}
        self.base_shas = {"Business": args.business_base_sha256, "Worker": args.worker_base_sha256}
        self.sha = args.package_sha256
        if self.sha != _sha(self.package):
            raise ValueError("Reviewed Lambda package digest differs")
        for logical in self.bases:
            package_changes(self.bases[logical], self.package, self.base_shas[logical], self.sha, self.changed)
        resources = self.target.cf.list_stack_resources(
            StackName=self.target.state["app"]["stackId"])["StackResourceSummaries"]
        functions = {r["LogicalResourceId"]: r["PhysicalResourceId"] for r in resources
                     if r["ResourceType"] == "AWS::Lambda::Function"
                     and r["LogicalResourceId"] in self.bases}
        if set(functions) != set(self.bases):
            raise ValueError("Could not bind both existing Studio Lambdas")
        previous_receipt = args.previous_receipt.resolve()
        for logical in self.bases:
            previous_sha = predecessor(
                previous_receipt, self.target.state, logical, functions[logical])
            if previous_sha != self.base_shas[logical]:
                raise ValueError("Current Lambda base differs from the audited predecessor")
        artifact = self.target.state["artifacts"]["outputs"]["Bucket"]
        expected = {
            "target": self.target.binding,
            "stack_id": self.target.state["app"]["stackId"],
            "previous_receipt": str(previous_receipt),
            "base_paths": {logical: str(path) for logical, path in self.bases.items()},
            "package_path": str(self.package),
            "functions": {
                logical: {"function": functions[logical], "base_sha256": self.base_shas[logical],
                          "patch_sha256": self.sha, "changed_zip_members": self.changed}
                for logical in ("Business", "Worker")},
            "upload": {"bucket": artifact, "key": "journey/" + self.kind + "/" + self.sha + ".zip",
                       "digest": self.sha},
        }
        if self.path.exists():
            self.receipt = json.loads(self.path.read_text())
            for key in expected:
                if key == "upload":
                    if any(self.receipt[key].get(field) != value
                           for field, value in expected[key].items()):
                        raise ValueError("Retained Lambda upload intent differs")
                elif self.receipt.get(key) != expected[key]:
                    raise ValueError("Retained Lambda release target differs")
        else:
            self.receipt = {**expected, "operations": {}}
            self.save()
        self.clients = {}

    def client(self, name):
        if name not in self.clients:
            self.clients[name] = self.target.session.client(name, config=Config(
                retries={"total_max_attempts": 1}, connect_timeout=10,
                read_timeout=300 if name == "s3" else 60,
                request_checksum_calculation="when_required"))
        return self.clients[name]

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.receipt, indent=2) + "\n")
        self.path.chmod(0o600)

    def verify_upload(self):
        upload = self.receipt["upload"]
        if not upload.get("version"):
            raise ValueError("Lambda artifact upload has no reconciled version")
        s3 = self.client("s3")
        head = s3.head_object(Bucket=upload["bucket"], Key=upload["key"],
                              VersionId=upload["version"])
        if (head["ContentLength"] != self.package.stat().st_size
                or head["Metadata"].get("sha256") != self.sha):
            raise ValueError("Uploaded Lambda artifact differs from the reviewed package")
        tags = s3.get_object_tagging(Bucket=upload["bucket"], Key=upload["key"],
                                     VersionId=upload["version"])["TagSet"]
        if {tag["Key"]: tag["Value"] for tag in tags}.get("auto-delete") != "no":
            raise ValueError("Lambda artifact lacks the retention tag")
        return upload

    def upload(self):
        upload = self.receipt["upload"]
        if not upload.get("version") and upload.get("multipart", {}).get("phase") == "COMPLETING":
            try:
                head = self.client("s3").head_object(Bucket=upload["bucket"], Key=upload["key"])
            except ClientError as exc:
                raise RuntimeError("Lambda upload completion is uncertain; inspect S3 before any retry") from exc
            if (head["ContentLength"] != self.package.stat().st_size
                    or head["Metadata"].get("sha256") != self.sha):
                raise ValueError("Lambda upload outcome differs")
            upload["version"] = head["VersionId"]
            self.save()
        if not upload.get("version"):
            multipart_helper()(self.client("s3"), self.package, upload, self.save)
        self.verify_upload()
        print("Scoped Lambda artifact verified:", self.sha, flush=True)

    def update(self, logical):
        upload = self.verify_upload()
        item = self.receipt["functions"][logical]
        name = item["function"]
        current = self.client("lambda").get_function_configuration(FunctionName=name)
        operations = self.receipt["operations"]
        intent = operations.get(logical)
        if intent is None:
            if (current["State"] != "Active" or current["LastUpdateStatus"] != "Successful"
                    or current["CodeSha256"] != _lambda_sha(item["base_sha256"])):
                raise ValueError("Live Lambda no longer matches its reviewed base")
            intent = {"function": name, "revision_before": current["RevisionId"], "phase": "INTENT"}
            operations[logical] = intent
            self.save()
            self.client("lambda").update_function_code(
                FunctionName=name, S3Bucket=upload["bucket"], S3Key=upload["key"],
                S3ObjectVersion=upload["version"], RevisionId=intent["revision_before"])
            intent["phase"] = "ACKNOWLEDGED"
            self.save()
        elif intent["function"] != name:
            raise ValueError("Retained Lambda identity changed")
        for _ in range(60):
            current = self.client("lambda").get_function_configuration(FunctionName=name)
            if current.get("LastUpdateStatus") != "InProgress":
                break
            time.sleep(2)
        status = reconcile_code(intent, current, _lambda_sha(item["base_sha256"]),
                                _lambda_sha(self.sha))
        if status != "verified":
            raise RuntimeError("Lambda update is not verified; reconcile the retained intent, do not retry")
        intent.update(phase="VERIFIED", revision_after=current["RevisionId"])
        self.save()
        print(logical + " Lambda verified: " + self.sha, flush=True)

    def audit(self):
        self.verify_upload()
        for logical in ("Worker", "Business"):
            if self.receipt["operations"].get(logical, {}).get("phase") != "VERIFIED":
                raise ValueError("Both Lambda updates must be verified")
            self.update(logical)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("upload", "worker", "business", "audit"))
    parser.add_argument("--release-kind", choices=tuple(RELEASES), default="consent-query")
    target_arguments(parser)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--previous-receipt", required=True, type=Path)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--package-sha256", required=True)
    parser.add_argument("--business-base", required=True, type=Path)
    parser.add_argument("--business-base-sha256", required=True)
    parser.add_argument("--worker-base", required=True, type=Path)
    parser.add_argument("--worker-base-sha256", required=True)
    args = parser.parse_args()
    release = ScopedRelease(args)
    if args.action in ("business", "worker"):
        release.update(args.action.title())
    else:
        getattr(release, args.action)()
