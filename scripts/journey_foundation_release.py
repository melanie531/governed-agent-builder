"""Receipt-bound foundation-only hotfix for an existing, explicitly bound Studio."""
import argparse
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import time

from botocore.config import Config
from botocore.exceptions import ClientError

from backend.dynamo_store import DynamoStore
from backend.foundation_runs import get, put
from scripts.deployment_target import DeploymentTarget, target_arguments


def multipart_helper():
    source = Path(__file__).resolve().parents[1] / "examples/runtime-snowflake-mcp/artifact_upload.py"
    spec = importlib.util.spec_from_file_location("journey_artifact_upload", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.multipart_upload


def activated_platform(previous, artifact):
    return {**previous, "artifact": artifact, "gateway_force_auth_v1": True}


class FoundationRelease:
    def __init__(self, args):
        self.target = DeploymentTarget(args.expected_account, args.profile, args.region, args.state)
        self.package, self.path = args.package.resolve(), args.receipt.resolve()
        sha = hashlib.sha256(self.package.read_bytes()).hexdigest()
        if sha != args.package_sha256:
            raise ValueError("Foundation package digest differs from the reviewed artifact")
        current = self.target.state["journeyPlatform"]["artifact"]
        self.artifact = {"bucket": current["bucket"], "key": "journey/foundation/" + sha + ".zip",
                         "sha256": sha}
        if current["sha256"] not in (args.base_sha256, sha):
            raise ValueError("Current platform foundation differs from this release")
        if current["sha256"] == sha and not self.path.exists():
            raise ValueError("Refusing to adopt an unrecorded foundation activation")
        base = (json.loads(self.path.read_text())["base_artifact"] if self.path.exists() else current)
        if base["sha256"] != args.base_sha256 or base["bucket"] != current["bucket"]:
            raise ValueError("Reviewed base foundation differs from the retained release")
        expected = {"target": self.target.binding, "stack_id": self.target.state["app"]["stackId"],
                    "base_artifact": base, "artifact": self.artifact}
        if self.path.exists():
            self.receipt = json.loads(self.path.read_text())
            if any(self.receipt.get(key) != value for key, value in expected.items()):
                raise ValueError("Retained foundation release intent differs")
        else:
            self.receipt = expected
            self.save()
        self.clients = {}

    def client(self, name):
        if name not in self.clients:
            self.clients[name] = self.target.session.client(name, config=Config(
                retries={"total_max_attempts": 1}, connect_timeout=10, read_timeout=300,
                request_checksum_calculation="when_required"))
        return self.clients[name]

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.receipt, indent=2, default=str) + "\n")
        self.path.chmod(0o600)

    def verify_upload(self):
        record = self.receipt.get("upload", {})
        if not record.get("version"):
            raise ValueError("Upload has no reconciled object version")
        head = self.client("s3").head_object(Bucket=self.artifact["bucket"], Key=self.artifact["key"],
                                              VersionId=record["version"])
        if (head["ContentLength"] != self.package.stat().st_size
                or head["Metadata"].get("sha256") != self.artifact["sha256"]):
            raise ValueError("Uploaded foundation object differs from the reviewed package")
        tags = self.client("s3").get_object_tagging(Bucket=self.artifact["bucket"], Key=self.artifact["key"],
                                                     VersionId=record["version"])["TagSet"]
        if {tag["Key"]: tag["Value"] for tag in tags}.get("auto-delete") != "no":
            raise ValueError("Foundation object lacks the required retention tag")
        return {**self.artifact, "version_id": record["version"]}

    def upload(self):
        record = self.receipt.setdefault("upload", {"bucket": self.artifact["bucket"],
                                                     "key": self.artifact["key"],
                                                     "digest": self.artifact["sha256"]})
        if {key: record[key] for key in ("bucket", "key", "digest")} != {
                "bucket": self.artifact["bucket"], "key": self.artifact["key"],
                "digest": self.artifact["sha256"]}:
            raise ValueError("Retained upload target differs")
        self.save()
        if not record.get("version") and record.get("multipart", {}).get("phase") == "COMPLETING":
            try:
                head = self.client("s3").head_object(Bucket=record["bucket"], Key=record["key"])
            except ClientError as exc:
                if exc.response["Error"]["Code"] not in ("404", "NoSuchKey"):
                    raise
                raise RuntimeError("Multipart completion is uncertain; reconcile the retained upload before retry") from exc
            if head["ContentLength"] != self.package.stat().st_size or head["Metadata"].get("sha256") != record["digest"]:
                raise ValueError("Object after uncertain completion differs")
            record["version"] = head["VersionId"]
            self.save()
        if not record.get("version"):
            multipart_helper()(self.client("s3"), self.package, record, self.save)
        self.verify_upload()
        print("Foundation artifact verified:", record["digest"], flush=True)

    def activate(self):
        artifact = self.verify_upload()
        intent = self.receipt.get("activation")
        if intent is None:
            previous = self.target.state["journeyPlatform"]
            if previous["artifact"] != self.receipt["base_artifact"]:
                raise ValueError("Platform configuration changed before activation")
            intent = {"before": previous, "after": activated_platform(previous, artifact), "phase": "INTENT"}
            self.receipt["activation"] = intent
            self.save()
        elif intent["after"] != activated_platform(intent["before"], artifact):
            raise ValueError("Retained foundation activation differs")
        store = DynamoStore(self.target.state["app"]["outputs"]["StateTable"],
                            self.target.session.resource("dynamodb"))
        marker = "foundation-protocol-fix:" + artifact["sha256"]
        with store.tx() as db:
            current = get(db, "journey-platform")
            if current == intent["before"]:
                put(db, "journey-platform", intent["after"])
                put(db, marker, {"sha256": artifact["sha256"], "version_id": artifact["version_id"]})
            elif current != intent["after"] or get(db, marker) != {
                    "sha256": artifact["sha256"], "version_id": artifact["version_id"]}:
                raise ValueError("Current foundation changed outside this release")
        self.target.save("journeyPlatform", intent["after"])
        self.target.save("journeyRelease", {**self.target.state["journeyRelease"], "artifact": artifact})
        intent["phase"] = "VERIFIED"
        self.save()
        print("Future agent versions now use the patched foundation artifact.", flush=True)

    def recycle_business(self, lambda_receipt):
        if self.receipt.get("activation", {}).get("phase") != "VERIFIED":
            raise ValueError("Foundation activation must be verified first")
        old = json.loads(lambda_receipt.read_text())["artifact_uploads"]["lambda"]
        if (old["bucket"] != self.artifact["bucket"]
                or old["digest"] != self.target.state["journeyRelease"]["lambdaSha256"]
                or old["key"] != self.target.state["journeyRelease"]["lambdaKey"]):
            raise ValueError("Backend artifact differs from the retained application release")
        self.client("s3").head_object(Bucket=old["bucket"], Key=old["key"], VersionId=old["version"])
        resources = self.client("cloudformation").list_stack_resources(
            StackName=self.target.state["app"]["stackId"])["StackResourceSummaries"]
        business = [r for r in resources if r["LogicalResourceId"] == "Business"
                    and r["ResourceType"] == "AWS::Lambda::Function"]
        if len(business) != 1:
            raise ValueError("Could not identify the bound Business Lambda")
        name = business[0]["PhysicalResourceId"]
        expected = base64.b64encode(bytes.fromhex(old["digest"])).decode()
        current = self.client("lambda").get_function_configuration(FunctionName=name)
        if current["CodeSha256"] != expected or current.get("LastUpdateStatus") != "Successful":
            raise ValueError("Business Lambda is not on the retained application release")
        record = self.receipt.get("business_reload")
        if record is None:
            record = {"function": name, "revision_before": current["RevisionId"], "phase": "INTENT"}
            self.receipt["business_reload"] = record
            self.save()
            request = {"FunctionName": name, "S3Bucket": old["bucket"], "S3Key": old["key"],
                       "S3ObjectVersion": old["version"], "RevisionId": record["revision_before"]}
            self.client("lambda").update_function_code(**request)
            record["phase"] = "ACKNOWLEDGED"
            self.save()
        elif record["function"] != name:
            raise ValueError("Retained Business Lambda identity changed")
        for _ in range(60):
            current = self.client("lambda").get_function_configuration(FunctionName=name)
            if current["LastUpdateStatus"] != "InProgress":
                break
            time.sleep(2)
        if (current["CodeSha256"] != expected or current["LastUpdateStatus"] != "Successful"
                or current["RevisionId"] == record["revision_before"]):
            raise RuntimeError("Business Lambda reload requires manual GET reconciliation")
        if record["phase"] == "VERIFIED" and current["RevisionId"] != record["revision_after"]:
            raise ValueError("Business Lambda changed after the verified reload")
        record.update(phase="VERIFIED", revision_after=current["RevisionId"])
        self.save()
        print("Business Lambda reloaded the new platform setting.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("upload", "activate", "recycle-business"))
    target_arguments(parser)
    parser.add_argument("--base-sha256", required=True)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--package-sha256", required=True)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--lambda-receipt", type=Path)
    args = parser.parse_args()
    release = FoundationRelease(args)
    if args.action == "recycle-business":
        if args.lambda_receipt is None:
            parser.error("--lambda-receipt is required for recycle-business")
        release.recycle_business(args.lambda_receipt)
    else:
        getattr(release, args.action)()
