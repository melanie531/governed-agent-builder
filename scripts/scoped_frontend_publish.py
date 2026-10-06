"""Publish only the reviewed Studio JS asset and HTML shell to the bound distribution."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import time

from botocore.config import Config
from botocore.exceptions import ClientError

from scripts.deployment_target import DeploymentTarget, target_arguments
from scripts.mcp_onboarding_deploy import verified_onboarding_release
from scripts.mcp_onboarding_audit import (
    verified_scoped_connected_popup_release, verified_scoped_consent_query_release,
    verified_scoped_reauth_release,
    verified_scoped_reauth_idle_release,
    verified_scoped_native_gateway_release,
    verified_scoped_callback_resume_release,
    verified_scoped_session_auth_release,
)

RELEASE_VERIFIERS = {
    "onboarding": verified_onboarding_release,
    "consent-query": verified_scoped_consent_query_release,
    "connected-popup": verified_scoped_connected_popup_release,
    "reauth": verified_scoped_reauth_release,
    "reauth-idle": verified_scoped_reauth_idle_release,
    "native-gateway": verified_scoped_native_gateway_release,
    "callback-resume": verified_scoped_callback_resume_release,
    "session-auth": verified_scoped_session_auth_release,
}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def bundle_manifest(dist):
    index = (dist / "index.html").read_bytes()
    html = index.decode("utf-8")
    scripts = re.findall(r'<script[^>]+src="/(assets/[^"]+\.js)"', html)
    styles = re.findall(r'<link[^>]+href="/(assets/[^"]+\.css)"', html)
    if len(scripts) != 1 or len(styles) != 1:
        raise ValueError("Built HTML must have one script and one stylesheet asset references")
    for key in (*scripts, *styles):
        if not re.fullmatch(r"assets/index-[A-Za-z0-9_-]+\.(?:js|css)", key):
            raise ValueError("Built HTML asset references are not release-bounded")
        if not (dist / key).is_file():
            raise ValueError("Built HTML references missing assets")
    icon = "favicon.svg" if 'href="/favicon.svg"' in html else None
    if icon and not (dist / icon).is_file():
        raise ValueError("Built HTML references missing icon asset")
    return {"index_sha256": _sha(index), "script": scripts[0],
            "script_sha256": _sha((dist / scripts[0]).read_bytes()),
            "style": styles[0], "style_sha256": _sha((dist / styles[0]).read_bytes()),
            "icon": icon, "icon_sha256": _sha((dist / icon).read_bytes()) if icon else None}


def previous_frontend_base(prior, stack_id, bucket, distribution_id):
    if prior.get("stack_id") != stack_id:
        raise ValueError("The previous frontend receipt is outside this Studio")
    if "frontend" in prior:
        source = prior["frontend"]
        if (source.get("bucket") != bucket or source.get("distribution_id") != distribution_id):
            raise ValueError("The previous frontend receipt is outside this Studio")
        return source["index_version_after"], source["index_sha256_after"]
    if (prior.get("bucket") != bucket or prior.get("distribution_id") != distribution_id
            or any(prior.get("operations", {}).get(key, {}).get("phase") != "VERIFIED"
                   for key in ("index", "script", "invalidation"))):
        raise ValueError("The previous frontend receipt is not verified for this Studio")
    index = prior["operations"]["index"]
    if (not index.get("version") or not isinstance(index.get("sha256"), str)
            or index["sha256"] != prior.get("manifest", {}).get("index_sha256")):
        raise ValueError("The previous frontend index is not verified")
    return index["version"], index["sha256"]


class FrontendPublish:
    def __init__(self, args):
        self.kind = args.release_kind
        self.target = DeploymentTarget(args.expected_account, args.profile, args.region, args.state)
        self.dist = args.dist.resolve()
        self.manifest = bundle_manifest(self.dist)
        self.path = args.receipt.resolve()
        self.backend_receipt = args.backend_receipt.resolve()
        self.previous_receipt = args.previous_receipt.resolve()
        self.clients = {}
        outputs = self.target.state["app"]["outputs"]
        resources = self.target.cf.list_stack_resources(
            StackName=self.target.state["app"]["stackId"])["StackResourceSummaries"]
        functions = {r["LogicalResourceId"]: r["PhysicalResourceId"] for r in resources
                     if r["ResourceType"] == "AWS::Lambda::Function"
                     and r["LogicalResourceId"] in ("Business", "Worker")}
        if set(functions) != {"Business", "Worker"}:
            raise ValueError("Existing Studio Lambda binding changed")
        for logical, name in functions.items():
            expected_sha = RELEASE_VERIFIERS[self.kind](
                self.backend_receipt, self.target.state, logical, name)
            live = self.client("lambda").get_function_configuration(FunctionName=name)
            if (live["CodeSha256"] != base64.b64encode(bytes.fromhex(expected_sha)).decode()
                    or live["State"] != "Active" or live["LastUpdateStatus"] != "Successful"):
                raise ValueError("Studio backend has not reached the reviewed release")
        prior = json.loads(self.previous_receipt.read_text())
        index_base_version, index_base_sha256 = previous_frontend_base(
            prior, self.target.state["app"]["stackId"], outputs["FrontendBucket"], outputs["DistributionId"])
        expected = {
            "target": self.target.binding, "stack_id": self.target.state["app"]["stackId"],
            "bucket": outputs["FrontendBucket"], "distribution_id": outputs["DistributionId"],
            "previous_receipt": str(self.previous_receipt),
            "backend_receipt": str(self.backend_receipt),
            "index_base_version": index_base_version,
            "index_base_sha256": index_base_sha256,
            "manifest": self.manifest,
        }
        if self.path.exists():
            self.receipt = json.loads(self.path.read_text())
            if any(self.receipt.get(key) != value for key, value in expected.items()):
                raise ValueError("Retained frontend release intent differs")
        else:
            current = self.read("index.html")
            if (current["version"] != expected["index_base_version"]
                    or current["sha256"] != expected["index_base_sha256"]):
                raise ValueError("Live frontend index differs from the reviewed predecessor")
            style = self.read(self.manifest["style"])
            if style["sha256"] != self.manifest["style_sha256"]:
                raise ValueError("Existing stylesheet differs from the local build")
            if self.manifest["icon"] and self.read(self.manifest["icon"])["sha256"] != self.manifest["icon_sha256"]:
                raise ValueError("Existing icon differs from the local build")
            if self._head(self.manifest["script"]) is not None:
                raise ValueError("New script key already exists outside this release")
            self.receipt = {**expected, "operations": {}}
            self.save()

    def client(self, name):
        if name not in self.clients:
            self.clients[name] = self.target.session.client(name, config=Config(
                retries={"total_max_attempts": 1}, connect_timeout=10, read_timeout=60))
        return self.clients[name]

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.receipt, indent=2) + "\n")
        self.path.chmod(0o600)

    def _head(self, key):
        try:
            return self.client("s3").head_object(Bucket=self.receipt["bucket"] if hasattr(self, "receipt") else
                                                  self.target.state["app"]["outputs"]["FrontendBucket"], Key=key)
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
                return None
            raise

    def read(self, key):
        head = self._head(key)
        if head is None:
            raise ValueError("Expected hosted asset is missing")
        bucket = self.target.state["app"]["outputs"]["FrontendBucket"]
        data = self.client("s3").get_object(Bucket=bucket, Key=key,
                                             VersionId=head["VersionId"])["Body"].read()
        return {"version": head["VersionId"], "etag": head["ETag"], "sha256": _sha(data)}

    def _tagged(self, key, version):
        tags = self._tags(key, version)
        return {tag["Key"]: tag["Value"] for tag in tags}.get("auto-delete") == "no"

    def _tags(self, key, version):
        return self.client("s3").get_object_tagging(
            Bucket=self.receipt["bucket"], Key=key, VersionId=version)["TagSet"]

    def retain_existing_asset(self, key, digest):
        actual = self.read(key)
        if actual["sha256"] != digest:
            raise ValueError("Previously published asset differs from the local build")
        operation = self.receipt["operations"].get("retain:" + key)
        if operation is None:
            operation = {"key": key, "version": actual["version"], "sha256": digest, "phase": "INTENT"}
            self.receipt["operations"]["retain:" + key] = operation
            self.save()
            tags = self._tags(key, actual["version"])
            current = {tag["Key"]: tag["Value"] for tag in tags}
            if current.get("auto-delete") not in (None, "no"):
                raise ValueError("Existing asset has a conflicting retention tag")
            if current.get("auto-delete") != "no":
                self.client("s3").put_object_tagging(
                    Bucket=self.receipt["bucket"], Key=key, VersionId=actual["version"],
                    Tagging={"TagSet": [*tags, {"Key": "auto-delete", "Value": "no"}]})
        elif (operation["key"] != key or operation["version"] != actual["version"]
              or operation["sha256"] != digest):
            raise ValueError("Retained existing-asset intent differs")
        if not self._tagged(key, actual["version"]):
            raise RuntimeError("Existing-asset tag outcome is uncertain; reconcile, do not retry")
        operation["phase"] = "VERIFIED"
        self.save()

    def publish_asset(self):
        key = self.manifest["script"]
        digest = self.manifest["script_sha256"]
        operation = self.receipt["operations"].get("script")
        if operation is None:
            operation = {"key": key, "sha256": digest, "phase": "INTENT"}
            self.receipt["operations"]["script"] = operation
            self.save()
            data = (self.dist / key).read_bytes()
            result = self.client("s3").put_object(
                Bucket=self.receipt["bucket"], Key=key, Body=data, IfNoneMatch="*",
                ServerSideEncryption="AES256", Metadata={"sha256": digest}, Tagging="auto-delete=no",
                ChecksumSHA256=base64.b64encode(hashlib.sha256(data).digest()).decode(),
                ContentType="application/javascript", CacheControl="public,max-age=31536000,immutable")
            operation.update(phase="ACKNOWLEDGED", version=result["VersionId"])
            self.save()
        elif operation["key"] != key or operation["sha256"] != digest:
            raise ValueError("Retained script upload differs")
        actual = self.read(key)
        if actual["sha256"] != digest or not self._tagged(key, actual["version"]):
            raise ValueError("Published script differs from the reviewed asset or lacks retention")
        if operation.get("version") and operation["version"] != actual["version"]:
            raise ValueError("Published script version changed")
        operation.update(phase="VERIFIED", version=actual["version"])
        self.save()

    def publish_index(self):
        if self.receipt["operations"].get("script", {}).get("phase") != "VERIFIED":
            raise ValueError("New script must be verified before publishing HTML")
        operation = self.receipt["operations"].get("index")
        if operation is None:
            before = self.read("index.html")
            if (before["version"] != self.receipt["index_base_version"]
                    or before["sha256"] != self.receipt["index_base_sha256"]):
                raise ValueError("Hosted HTML changed before publication")
            operation = {"revision_before": before["version"], "etag_before": before["etag"],
                         "sha256": self.manifest["index_sha256"], "phase": "INTENT"}
            self.receipt["operations"]["index"] = operation
            self.save()
            data = (self.dist / "index.html").read_bytes()
            result = self.client("s3").put_object(
                Bucket=self.receipt["bucket"], Key="index.html", Body=data,
                IfMatch=before["etag"], ServerSideEncryption="AES256",
                Metadata={"sha256": operation["sha256"]}, Tagging="auto-delete=no",
                ChecksumSHA256=base64.b64encode(hashlib.sha256(data).digest()).decode(),
                ContentType="text/html", CacheControl="no-cache")
            operation.update(phase="ACKNOWLEDGED", version=result["VersionId"])
            self.save()
        actual = self.read("index.html")
        if (actual["sha256"] != self.manifest["index_sha256"]
                or actual["version"] == operation["revision_before"]
                or not self._tagged("index.html", actual["version"])):
            raise RuntimeError("Hosted HTML publication is uncertain; reconcile, do not retry")
        if operation.get("version") and actual["version"] != operation["version"]:
            raise ValueError("Hosted HTML version changed")
        operation.update(phase="VERIFIED", version=actual["version"])
        self.save()

    def invalidate(self):
        if self.receipt["operations"].get("index", {}).get("phase") != "VERIFIED":
            raise ValueError("HTML publication must be verified before invalidation")
        operation = self.receipt["operations"].get("invalidation")
        if operation is None:
            operation = {"caller_reference": self.kind + "-" + self.manifest["index_sha256"][:32],
                         "path": "/*", "phase": "INTENT"}
            self.receipt["operations"]["invalidation"] = operation
            self.save()
            response = self.client("cloudfront").create_invalidation(
                DistributionId=self.receipt["distribution_id"],
                InvalidationBatch={"Paths": {"Quantity": 1, "Items": ["/*"]},
                                   "CallerReference": operation["caller_reference"]})
            operation.update(phase="ACKNOWLEDGED", id=response["Invalidation"]["Id"])
            self.save()
        if not operation.get("id"):
            raise RuntimeError("CloudFront invalidation outcome is uncertain; reconcile without retry")
        for _ in range(120):
            current = self.client("cloudfront").get_invalidation(
                DistributionId=self.receipt["distribution_id"], Id=operation["id"])["Invalidation"]
            if (current["InvalidationBatch"]["CallerReference"] != operation["caller_reference"]
                    or current["InvalidationBatch"]["Paths"]["Items"] != [operation["path"]]):
                raise ValueError("CloudFront invalidation differs from the retained request")
            if current["Status"] == "Completed":
                operation["phase"] = "VERIFIED"
                self.save()
                return
            time.sleep(5)
        raise RuntimeError("CloudFront invalidation remains pending; retain its ID and reconcile")

    def publish(self):
        self.retain_existing_asset(self.manifest["style"], self.manifest["style_sha256"])
        if self.manifest["icon"]:
            self.retain_existing_asset(self.manifest["icon"], self.manifest["icon_sha256"])
        self.publish_asset()
        self.publish_index()
        self.invalidate()
        self.audit()
        print("Existing Studio frontend published and invalidation verified.", flush=True)

    def audit(self):
        if any(self.receipt["operations"].get(key, {}).get("phase") != "VERIFIED"
               for key in ("script", "index", "invalidation")):
            raise ValueError("Frontend release phases are incomplete")
        if (self.read("index.html")["sha256"] != self.manifest["index_sha256"]
                or self.read(self.manifest["script"])["sha256"] != self.manifest["script_sha256"]
                or self.read(self.manifest["style"])["sha256"] != self.manifest["style_sha256"]):
            raise ValueError("Hosted frontend asset digest differs")
        if self.manifest["icon"] and self.read(self.manifest["icon"])["sha256"] != self.manifest["icon_sha256"]:
            raise ValueError("Hosted icon asset digest differs")
        for key, operation in (("index.html", self.receipt["operations"]["index"]),
                               (self.manifest["script"], self.receipt["operations"]["script"])):
            if not self._tagged(key, operation["version"]):
                raise ValueError("Hosted frontend object lacks retention tag")
        for key in (self.manifest["style"], self.manifest["icon"]):
            if key and (self.receipt["operations"].get("retain:" + key, {}).get("phase") != "VERIFIED"
                        or not self._tagged(key, self.read(key)["version"])):
                raise ValueError("Referenced frontend asset lacks verified retention")
        invalidation = self.client("cloudfront").get_invalidation(
            DistributionId=self.receipt["distribution_id"],
            Id=self.receipt["operations"]["invalidation"]["id"])["Invalidation"]
        if invalidation["Status"] != "Completed":
            raise ValueError("CloudFront invalidation is not completed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("publish", "audit"))
    parser.add_argument("--release-kind", choices=tuple(RELEASE_VERIFIERS), default="consent-query")
    target_arguments(parser)
    parser.add_argument("--dist", required=True, type=Path)
    parser.add_argument("--backend-receipt", required=True, type=Path)
    parser.add_argument("--previous-receipt", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    getattr(FrontendPublish(args), args.action)()
