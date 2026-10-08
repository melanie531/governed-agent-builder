"""Publish the platform Catalog and deploy its owned AgentCore integration.

Every command requires an explicit account/profile/region and the existing bound
release-state file. Initial setup creates no remote MCP target or credential.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from infra.journey import template as platform_template
from infra.serverless import template as app_template
from scripts.deployment_target import target_arguments

STACK = "governed-agent-builder-journey"
TAGS = {"project": "governed-agent-builder", "journey": "create-agent", "auto-delete": "no"}


def wait(read, *, timeout=900):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        status = value.get("status", value.get("StackStatus"))
        print("Resource status:", status, flush=True)
        if status in ("READY", "CREATE_COMPLETE", "UPDATE_COMPLETE"):
            return value
        if status in ("FAILED", "CREATE_FAILED", "UPDATE_FAILED", "UPDATE_ROLLBACK_COMPLETE", "ROLLBACK_COMPLETE"):
            raise RuntimeError("Resource failed: " + json.dumps(value.get("statusReasons", [])))
        time.sleep(10)
    raise TimeoutError("Resource did not become ready")


def upload(target):
    bucket = target.state["artifacts"]["outputs"]["Bucket"]
    source = getattr(target, "package_path", ROOT / "artifacts/serverless-release.zip")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    key = f"releases/{sha}/lambda.zip"
    from botocore.config import Config
    from boto3.s3.transfer import TransferConfig
    # Sign the payload hash instead of streaming an optional checksum trailer.
    # Some operator HTTPS proxies stall large aws-chunked request bodies.
    s3 = target.session.client("s3", config=Config(
        connect_timeout=5, read_timeout=60, retries={"max_attempts": 2},
        request_checksum_calculation="when_required", response_checksum_validation="when_required",
        s3={"payload_signing_enabled": True}))
    transfer = TransferConfig(multipart_threshold=5 * 1024 * 1024, multipart_chunksize=5 * 1024 * 1024, max_concurrency=2)
    runtime = getattr(target, "runtime_package_path", ROOT / "artifacts/journey-runtime.zip")
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(runtime, "w", zipfile.ZIP_DEFLATED) as output:
        for info in original.infolist():
            if info.filename.startswith(("backend/", "scripts/", "tools/", "foundations/")) or info.filename == "uv.lock":
                continue
            output.writestr(info, original.read(info))
        output.writestr(zipfile.ZipInfo("main.py", date_time=(2026, 1, 1, 0, 0, 0)),
                        "from runtime.journey.main import create_app\ncreate_app().run()\n")
    runtime_sha = hashlib.sha256(runtime.read_bytes()).hexdigest()
    runtime_key = f"journey/foundation/{runtime_sha}.zip"
    previous = target.state.get("journeyRelease", {})
    if (previous.get("lambdaSha256") == sha and previous.get("artifact", {}).get("sha256") == runtime_sha
            and previous["artifact"]["bucket"] == bucket and previous["artifact"]["key"] == runtime_key):
        artifact = previous["artifact"]
        s3.head_object(Bucket=bucket, Key=runtime_key, VersionId=artifact["version_id"])
        print("Reusing verified release artifact", flush=True)
        return bucket, key, artifact
    print("Uploading versioned deployment artifacts", flush=True)
    s3.upload_file(str(source), bucket, key, ExtraArgs={"ServerSideEncryption": "AES256"}, Config=transfer)
    print("Lambda artifact uploaded", flush=True)
    if previous.get("artifact", {}).get("sha256") == runtime_sha and previous["artifact"]["bucket"] == bucket:
        artifact = previous["artifact"]
        s3.head_object(Bucket=bucket, Key=runtime_key, VersionId=artifact["version_id"])
    else:
        s3.upload_file(str(runtime), bucket, runtime_key, ExtraArgs={"ServerSideEncryption": "AES256"}, Config=transfer)
        uploaded = s3.head_object(Bucket=bucket, Key=runtime_key)
        if uploaded.get("VersionId") in (None, "null"):
            raise RuntimeError("Foundation artifact bucket must be versioned")
        artifact = {"bucket": bucket, "key": runtime_key, "version_id": uploaded["VersionId"], "sha256": runtime_sha}
    target.save("journeyRelease", {"lambdaKey": key, "lambdaSha256": sha, "artifact": artifact})
    return bucket, key, artifact


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "activate", "publish-ui"])
    parser.add_argument("--worker-memory-size", type=int, help="First installation worker memory in MiB (512–10240)")
    target_arguments(parser)
    args = parser.parse_args()
    from scripts.bootstrap_support import PlatformTarget, NoRetrySession
    target = PlatformTarget(args.expected_account, args.profile, args.region, args.state)
    if args.action == "prepare":
        target.session = NoRetrySession(target.session, target)
        target.cf = target.session.client("cloudformation")
        from scripts.platform_install import prepare
        prepare(target, worker_memory_size=args.worker_memory_size)
    else:
        from scripts import serverless_deploy as release
        release.TARGET, release.SESSION, release.CF, release.STATE = target, target.session, target.cf, target.path
        if args.action == "activate":
            settings = target.state["journeyPlatform"]
            release.deploy("app", app_template(journey=settings), {
                "ArtifactBucket": target.state["artifacts"]["outputs"]["Bucket"],
                "ArtifactKey": target.state["journeyRelease"]["lambdaKey"]})
            target.save("releaseSha256", target.state["journeyRelease"]["lambdaSha256"])
        else:
            release.main("publish")


if __name__ == "__main__":
    main()
