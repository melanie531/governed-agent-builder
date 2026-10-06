"""Receipt-bound session renewal patches on the four existing Studio Lambdas.

Packages are prepared from each retained live ZIP, preserving every other member.
This command does not create resources, change configuration, or rebuild a dirty
checkout. Every cloud write is journaled before dispatch.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import time

from botocore.config import Config

from scripts.deployment_target import DeploymentTarget, target_arguments
from scripts.journey_foundation_release import multipart_helper
from scripts.scoped_consent_query_release import package_changes, reconcile_code

AUTH_CHANGED = ["backend/hosted_auth.py", "backend/repository.py"]
APP_CHANGED = ["backend/hosted_auth.py", "backend/journey.py", "backend/repository.py", "backend/serverless.py"]
CHANGED = {"Auth": AUTH_CHANGED, "Authorizer": AUTH_CHANGED,
           "Business": APP_CHANGED, "Worker": APP_CHANGED}
ORDER = ("Worker", "Business", "Authorizer", "Auth")


def configuration_digest(value):
    value = dict(value)
    for key in ("ResponseMetadata", "CodeSha256", "CodeSize", "LastModified", "RevisionId",
                "State", "StateReason", "StateReasonCode", "LastUpdateStatus",
                "LastUpdateStatusReason", "LastUpdateStatusReasonCode"):
        value.pop(key, None)
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def configuration_matches(value, item):
    if configuration_digest(value) == item["configuration_sha256"]:
        return True
    patch = item.get("managed_runtime_patch") or {}
    if (patch.get("update_runtime_on") != "Auto"
            or value.get("RuntimeVersionConfig") != patch.get("after") or not patch.get("before")):
        return False
    # Hash the complete configuration after restoring only the exact prior AWS
    # runtime version. IAM, environment, handler, timeout and every other field
    # must still match the original pre-deployment fingerprint.
    restored = {**value, "RuntimeVersionConfig": patch["before"]}
    return configuration_digest(restored) == item["configuration_sha256"]


def verify_release(path, state, logical, function_name, *, complete=True):
    from scripts.mcp_onboarding_audit import verified_scoped_session_auth_release

    receipt = json.loads(Path(path).read_text())
    if (receipt.get("target") != state["target"]
            or receipt.get("stack_id") != state["app"]["stackId"]
            or set(receipt.get("functions", {})) != set(CHANGED) or logical not in CHANGED):
        raise ValueError("Scoped session renewal target differs")
    item = receipt["functions"][logical]
    if item.get("function") != function_name or item.get("changed_zip_members") != CHANGED[logical]:
        raise ValueError("Scoped session renewal function or member scope differs")
    expected_base = (verified_scoped_session_auth_release(
        Path(receipt["previous_receipt"]), state, logical, function_name)
        if logical in ("Business", "Worker") else
        state.get("releaseSha256", state.get("release", {}).get("sha256")))
    if item.get("base_sha256") != expected_base:
        raise ValueError("Scoped session renewal predecessor differs")
    package_changes(Path(item["base_path"]), Path(item["package_path"]),
                    expected_base, item["patch_sha256"], CHANGED[logical])
    upload = item["upload"]
    if (upload.get("bucket") != state["artifacts"]["outputs"]["Bucket"]
            or upload.get("digest") != item["patch_sha256"]
            or upload.get("key") != "journey/session-renewal/" + item["patch_sha256"] + ".zip"):
        raise ValueError("Scoped session renewal artifact binding differs")
    if complete and (receipt.get("operations", {}).get(logical, {}).get("phase") != "VERIFIED"
                     or not upload.get("version")):
        raise ValueError("Scoped session renewal is not verified")
    return item["patch_sha256"]


class SessionRelease:
    def __init__(self, args):
        self.target = DeploymentTarget(args.expected_account, args.profile, args.region, args.state)
        self.path = args.receipt.resolve()
        self.receipt = json.loads(self.path.read_text())
        self.clients = {}
        resources = self.target.cf.list_stack_resources(
            StackName=self.target.state["app"]["stackId"])["StackResourceSummaries"]
        functions = {r["LogicalResourceId"]: r["PhysicalResourceId"] for r in resources
                     if r["ResourceType"] == "AWS::Lambda::Function" and r["LogicalResourceId"] in CHANGED}
        if set(functions) != set(CHANGED):
            raise ValueError("Existing session Lambdas could not be bound")
        for logical, name in functions.items():
            verify_release(self.path, self.target.state, logical, name, complete=False)

    def client(self, name):
        if name not in self.clients:
            self.clients[name] = self.target.session.client(name, config=Config(
                retries={"total_max_attempts": 1}, connect_timeout=10,
                read_timeout=300 if name == "s3" else 20,
                request_checksum_calculation="when_required"))
        return self.clients[name]

    def save(self):
        self.path.write_text(json.dumps(self.receipt, indent=2) + "\n")
        self.path.chmod(0o600)

    def verified_upload(self, logical):
        item = self.receipt["functions"][logical]
        upload = item["upload"]
        if not upload.get("version"):
            raise ValueError("Session artifact upload has no reconciled version")
        args = {"Bucket": upload["bucket"], "Key": upload["key"], "VersionId": upload["version"]}
        head = self.client("s3").head_object(**args)
        tags = self.client("s3").get_object_tagging(**args)["TagSet"]
        if (head["ContentLength"] != Path(item["package_path"]).stat().st_size
                or head.get("Metadata", {}).get("sha256") != item["patch_sha256"]
                or head.get("ServerSideEncryption") not in ("AES256", "aws:kms")
                or {t["Key"]: t["Value"] for t in tags}.get("auto-delete") != "no"):
            raise ValueError("Session artifact content, encryption or tags differ")
        return upload

    def upload(self):
        for logical in ORDER:
            item = self.receipt["functions"][logical]
            upload = item["upload"]
            # Identical function packages share the exact already-verified version.
            matching = next((other["upload"] for other in self.receipt["functions"].values()
                             if other["patch_sha256"] == item["patch_sha256"]
                             and other["upload"].get("version")), None)
            if matching and not upload.get("version"):
                upload.update(matching)
                self.save()
            if not upload.get("version") and upload.get("multipart", {}).get("phase") == "COMPLETING":
                head = self.client("s3").head_object(Bucket=upload["bucket"], Key=upload["key"])
                if (head.get("Metadata", {}).get("sha256") != item["patch_sha256"]
                        or head["ContentLength"] != Path(item["package_path"]).stat().st_size):
                    raise ValueError("Uncertain session upload did not match; reconcile without replay")
                upload["version"] = head["VersionId"]
                self.save()
            if not upload.get("version"):
                multipart_helper()(self.client("s3"), Path(item["package_path"]), upload, self.save)
            self.verified_upload(logical)
            print(logical + " retained artifact verified", flush=True)

    def update(self, logical):
        item = self.receipt["functions"][logical]
        upload = self.verified_upload(logical)
        client = self.client("lambda")
        current = client.get_function_configuration(FunctionName=item["function"])
        if not configuration_matches(current, item):
            raise ValueError("Lambda configuration changed outside this release")
        if item.get("managed_runtime_patch"):
            management = client.get_runtime_management_config(FunctionName=item["function"])
            if management.get("UpdateRuntimeOn") != "Auto":
                raise ValueError("Lambda runtime management changed outside this release")
        intent = self.receipt["operations"].get(logical)
        before = base64.b64encode(bytes.fromhex(item["base_sha256"])).decode()
        after = base64.b64encode(bytes.fromhex(item["patch_sha256"])).decode()
        if intent is None:
            if (current["State"] != "Active" or current["LastUpdateStatus"] != "Successful"
                    or current["CodeSha256"] != before or current["RevisionId"] != item["revision_id"]):
                raise ValueError("Lambda no longer matches the reviewed live base")
            intent = {"function": item["function"], "revision_before": current["RevisionId"], "phase": "INTENT"}
            self.receipt["operations"][logical] = intent
            self.save()
            client.update_function_code(FunctionName=item["function"], S3Bucket=upload["bucket"],
                S3Key=upload["key"], S3ObjectVersion=upload["version"], RevisionId=intent["revision_before"])
            intent["phase"] = "ACKNOWLEDGED"
            self.save()
        elif intent.get("function") != item["function"]:
            raise ValueError("Retained Lambda intent targets a different function")
        for _ in range(30):
            current = client.get_function_configuration(FunctionName=item["function"])
            if current.get("LastUpdateStatus") != "InProgress":
                break
            time.sleep(1)
        if reconcile_code(intent, current, before, after) != "verified":
            raise RuntimeError("Lambda update is uncertain; reconcile this intent without repeating it")
        if not configuration_matches(current, item):
            raise ValueError("Lambda configuration changed during the code update")
        intent.update(phase="VERIFIED", revision_after=current["RevisionId"])
        self.save()
        print(logical + " session renewal code verified", flush=True)

    def audit(self):
        for logical in ORDER:
            if self.receipt["operations"].get(logical, {}).get("phase") != "VERIFIED":
                raise ValueError("All four existing Lambdas must already be verified")
            self.update(logical)
            verify_release(self.path, self.target.state, logical,
                           self.receipt["functions"][logical]["function"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("upload", "audit", *CHANGED))
    target_arguments(parser)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    release = SessionRelease(args)
    if args.action in CHANGED:
        release.update(args.action)
    else:
        getattr(release, args.action)()
