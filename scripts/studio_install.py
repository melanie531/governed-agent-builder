"""Install Studio in a fresh AWS account/region from one operator configuration.

The pipeline owns generated settings, immutable uploads and native deployment
receipts. It does not create users, publish models or configure MCP providers.
"""
import argparse
import copy
from dataclasses import dataclass
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from types import SimpleNamespace

from foundation_harness.config import digest
from scripts.bootstrap_support import Journal, json_file
from scripts.studio_install_support import InstallTarget, install_stack, source_digest, source_manifest, upload_artifact

ROOT = Path(__file__).resolve().parents[1]
STAGES = (
    "preflight", "build", "artifacts", "application", "runtime-prerequisites",
    "platform", "registry", "credentials", "user-oauth", "package-hosting",
    "activate", "publish", "audit",
)


@dataclass(frozen=True)
class Configuration:
    account: str
    profile: str
    region: str
    root: Path
    evidence_name: str
    worker_memory_size: int = 512
    package_reserved_concurrency: int | None = None
    user_oauth: bool = True
    package_uploads: bool = True

    @property
    def binding(self):
        return {key: getattr(self, key) for key in ("account", "profile", "region")}

    @property
    def state(self):
        return self.root / "artifacts" / f"account-{self.account}-{self.region}" / "release-state.json"

    @property
    def evidence(self):
        return self.root / "artifacts" / self.evidence_name

    def intent(self):
        return {**self.binding, **{key: getattr(self, key) for key in (
            "evidence_name", "worker_memory_size", "package_reserved_concurrency",
            "user_oauth", "package_uploads")}}


def load_configuration(path, *, root=ROOT):
    value = json_file(path)
    allowed = {"account", "profile", "region", "evidence_name", "worker_memory_size",
               "package_reserved_concurrency", "user_oauth", "package_uploads"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("Installation configuration has unknown fields")
    for field in ("account", "profile", "region"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ValueError(f"Explicit {field} is required")
    if not re.fullmatch(r"[0-9]{12}", value["account"]):
        raise ValueError("Account must be a 12-digit string")
    if not re.fullmatch(
            r"(?:us|eu|ap|ca|sa|me|af|il|mx)-(?:north|south|east|west|central|northeast|southeast)-[1-9]",
            value["region"]):
        raise ValueError("Use a supported commercial AWS region")
    memory = value.get("worker_memory_size", 512)
    if type(memory) is not int or not 512 <= memory <= 10240:
        raise ValueError("Worker memory must be an integer from 512 to 10240 MiB")
    concurrency = value.get("package_reserved_concurrency")
    if concurrency is not None and (type(concurrency) is not int or concurrency < 1):
        raise ValueError("Package reserved concurrency must be null or a positive integer")
    if any(type(value[key]) is not bool for key in ("user_oauth", "package_uploads") if key in value):
        raise ValueError("Add-on options must be JSON booleans")
    name = value.get("evidence_name", f"install-{value['account']}-{value['region']}")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", name):
        raise ValueError("Evidence name must contain lowercase letters, digits and hyphens")
    return Configuration(**{**value, "evidence_name": name, "root": Path(root).resolve()})


def stage_names(config):
    disabled = set()
    if not config.user_oauth:
        disabled.add("user-oauth")
    if not config.package_uploads:
        disabled.add("package-hosting")
    return tuple(name for name in STAGES if name not in disabled)


def run_steps(target, steps, *, verify_completed=None):
    """Checkpoint orchestration; each native stage retains its own write receipts."""
    for name, run in steps.items():
        installation = copy.deepcopy(target.state["installation"])
        previous = installation["steps"].get(name, {})
        if previous.get("status") == "COMPLETE":
            if verify_completed:
                verify_completed(name)
            continue
        installation["steps"][name] = {"status": "RUNNING", "started_at": time.time()}
        target.save("installation", installation)
        print("Installation: " + name, flush=True)
        try:
            run()
        except Exception as error:
            installation = copy.deepcopy(target.state["installation"])
            installation["steps"][name].update(status="NEEDS_RECONCILIATION", error_type=type(error).__name__)
            target.save("installation", installation)
            raise
        installation = copy.deepcopy(target.state["installation"])
        installation["steps"][name].update(status="COMPLETE", completed_at=time.time())
        target.save("installation", installation)


def hosting_configuration(config, state, artifact, worker_role):
    if not worker_role.startswith(f"arn:aws:iam::{config.account}:role/"):
        raise ValueError("Worker role does not belong to this installation")
    return {
        "prefix": "studio-python-mcp", **config.binding,
        "bundle_name": "Python MCP hosting bundle", "bundle_digest": artifact["digest"],
        "reserved_concurrency": config.package_reserved_concurrency,
        "python_onboarding": {
            "table_name": state["app"]["outputs"]["StateTable"],
            "worker_role_name": worker_role.rsplit("/", 1)[-1],
            "runtime_prefix": "studio_python_mcp",
            "deployment_prefix": state["journeyPlatform"]["mcp_onboarding"]["credential_prefix"],
            "artifact": {"bucket": artifact["bucket"], "key": artifact["key"], "version_id": artifact["version"]},
        },
    }


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as output:
        temporary.chmod(0o600)
        json.dump(value, output, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


class Pipeline:
    def __init__(self, config):
        self.config = config
        source = source_digest(config.root)
        inputs = {"configuration": config.intent(), "source_sha256": source}
        input_path = config.evidence / "installation-inputs.json"
        if input_path.exists():
            if json_file(input_path) != inputs:
                raise RuntimeError("Installation evidence belongs to different inputs or another target")
        elif config.evidence.exists() and any(config.evidence.iterdir()):
            raise RuntimeError("Refusing to overwrite an unrecorded installation evidence directory")
        self.target = InstallTarget(config, source)
        config.evidence.mkdir(parents=True, exist_ok=True)
        write_json(input_path, inputs)
        write_json(config.evidence / "source-manifest.json", source_manifest(config.root))
        self.package = config.evidence / "lambda.zip"
        self.frontend = config.evidence / "frontend"
        self.bundle = config.evidence / "python-mcp-bundle.zip"
        self.artifact_receipt = config.evidence / "artifact-receipt.json"
        self.python_config = config.evidence / "python-hosting.json"
        self.example = config.root / "examples/runtime-snowflake-mcp"
        self.target.package_path = self.package
        self.target.runtime_package_path = config.evidence / "journey-runtime.zip"

    def command(self, args, *, cwd=None, env=None):
        subprocess.run([str(arg) for arg in args], cwd=cwd or self.config.root, env=env, check=True)

    def release(self):
        from scripts.mcp_onboarding_deploy import Release
        return Release(SimpleNamespace(
            expected_account=self.config.account, profile=self.config.profile, region=self.config.region,
            state=self.config.state, evidence_name=self.config.evidence_name, package=self.package))

    def verify_build(self):
        recorded = self.target.state.get("installationBuild")
        if recorded is None:
            raise RuntimeError("No verified installation build")
        for name, expected in recorded["files"].items():
            path = self.config.evidence / name
            if (not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
                raise RuntimeError("Pinned installation artifact changed: " + name)

    def verify_completed(self, name):
        if source_digest(self.config.root) != self.target.state["installation"]["source_sha256"]:
            raise RuntimeError("Pinned installation source changed")
        if name == "build":
            self.verify_build()

    def preflight(self):
        from infra.journey import template as journey_template
        from infra.serverless import artifacts_template, template
        from scripts.serverless_deploy import safety
        summaries = {}
        for name, body in (("artifacts", artifacts_template()), ("application", template()),
                           ("platform", journey_template())):
            safety(body)
            inline, managed = {}, {}
            for logical, resource in body["Resources"].items():
                properties = resource.get("Properties", {})
                if resource["Type"] == "AWS::IAM::Role":
                    inline[logical] = sum(len(json.dumps(p["PolicyDocument"], separators=(",", ":")))
                                          for p in properties.get("Policies", []))
                elif resource["Type"] == "AWS::IAM::ManagedPolicy":
                    managed[logical] = len(json.dumps(properties["PolicyDocument"], separators=(",", ":")))
            if (any(size >= 10240 for size in inline.values()) or any(size >= 6144 for size in managed.values())
                    or any(r["Type"] == "AWS::Lambda::Url" for r in body["Resources"].values())):
                raise RuntimeError("Infrastructure security or IAM size preflight failed")
            summaries[name] = {"template_sha256": digest(body), "inline_policy_bytes": inline,
                               "managed_policy_bytes": managed, "private_resources": True}
        # Fail early if the installed, locked SDK lacks a required service model.
        for service in ("bedrock-agentcore-control", "agent-registry-control"):
            self.target.session.client(service)
        write_json(self.config.evidence / "preflight.json",
                   {"account_verified": True, "existing_app_not_adopted": True, "templates": summaries})

    def build(self):
        from scripts.serverless_package import main as package
        self.command(["npm", "ci"], cwd=self.config.root / "frontend")
        self.command(["npm", "run", "build", "--", "--outDir", self.frontend],
                     cwd=self.config.root / "frontend")
        package(output=self.package)
        paths = [self.package, *[path for path in self.frontend.rglob("*") if path.is_file()]]
        if self.config.package_uploads:
            self.command(["uv", "sync", "--locked", "--python", "3.13", "--project", self.example,
                          "--group", "studio-installer"])
            self.command([self.example / ".venv/bin/python", "-c",
                          "import aws_cdk, backend.dynamo_store, backend.foundation_runs, backend.mcp_python_cloud"],
                         env={**os.environ, "PYTHONPATH": str(self.config.root)})
            self.command([sys.executable, self.example / "package.py", "--output", self.bundle])
            paths.append(self.bundle)
        if not (self.frontend / "index.html").is_file():
            raise RuntimeError("Frontend build did not produce index.html")
        self.target.save("installationBuild", {"files": {
            path.relative_to(self.config.evidence).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}})

    def artifacts(self):
        from infra.serverless import artifacts_template
        install_stack(self.target, "artifacts", artifacts_template())

    def application(self):
        from backend.dynamo_store import DynamoStore
        from infra.serverless import template
        content = self.package.read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        uploaded = upload_artifact(self.target, "lambda", f"releases/{sha}/lambda.zip", content)
        self.target.save("installationArtifacts", {"lambda": uploaded})
        write_json(self.artifact_receipt, {"target": self.config.binding, "artifact_uploads": {"lambda": uploaded}})
        outputs = install_stack(self.target, "app", template(),
                                {"ArtifactBucket": uploaded["bucket"], "ArtifactKey": uploaded["key"]})
        self.target.save("releaseSha256", sha)
        DynamoStore(outputs["StateTable"], self.target.session.resource("dynamodb")).initialize()

    def runtime_prerequisites(self):
        from scripts.agentcore_prerequisites import install
        install(self.target)

    def platform(self):
        from scripts.platform_install import prepare
        prepare(self.target, worker_memory_size=self.config.worker_memory_size)

    def registry(self):
        release = self.release()
        release.registry()
        release.configure()

    def credentials(self):
        from scripts.mcp_credentials_deploy import gateway
        gateway(self.release())

    def user_gateway(self):
        from scripts.mcp_oauth_gateway_deploy import UserGatewayRelease
        if json_file(self.artifact_receipt) != {
                "target": self.config.binding, "artifact_uploads": self.target.state["installationArtifacts"]}:
            raise RuntimeError("Generated artifact receipt changed")
        return UserGatewayRelease(self.release(), "studio-user-tools", self.artifact_receipt)

    def user_oauth(self):
        gateway = self.user_gateway()
        gateway.deploy()
        gateway.configure()
        gateway.activate()

    def facade(self, action):
        self.command([self.example / ".venv/bin/python", self.example / "facade_deploy.py", action,
                      "--python-config", self.python_config, "--state-dir", self.config.evidence / "package-hosting"],
                     env={**os.environ, "PYTHONPATH": str(self.config.root)})

    def package_hosting(self):
        content = self.bundle.read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        artifact = upload_artifact(self.target, "package-bundle", f"mcp/python/bundles/{sha}.zip", content)
        outputs = self.target.state["app"]["outputs"]
        worker = self.target.session.client("lambda").get_function_configuration(FunctionName=outputs["WorkerFunction"])
        value = hosting_configuration(self.config, self.target.state, artifact, worker["Role"])
        if self.python_config.exists() and json_file(self.python_config) != value:
            raise RuntimeError("Generated package hosting configuration changed")
        write_json(self.python_config, value)
        self.facade("deploy")
        self.facade("configure")
        self.release().configure_packages()

    def activate(self):
        from infra.serverless import template
        artifact = self.target.state["installationArtifacts"]["lambda"]
        body = template(journey=self.target.state["journeyPlatform"])
        phase = self.target.state["installation"].get("activation_phase", "activate")
        install_stack(self.target, "app", body,
                      {"ArtifactBucket": artifact["bucket"], "ArtifactKey": artifact["key"]}, phase=phase)
        release = self.release()
        release.tag_policies()
        physical = {}
        for page in release.client("cloudformation").get_paginator("list_stack_resources").paginate(
                StackName=self.target.state["app"]["stackId"]):
            physical.update({r["LogicalResourceId"]: r["PhysicalResourceId"] for r in page["StackResourceSummaries"]})
        release.verify_functions(body, physical, artifact["digest"])
        release.receipt.update(deployment_verified=True, release_sha256=artifact["digest"])
        release.save()

    def publish(self):
        outputs = self.target.state["app"]["outputs"]
        # Only upload the pinned build manifest, never stray files left in dist.
        files = {key.removeprefix("frontend/"): sha
                 for key, sha in self.target.state["installationBuild"]["files"].items()
                 if key.startswith("frontend/")}
        for name in sorted(files, key=lambda name: (name == "index.html", name)):
            upload_artifact(self.target, "frontend-" + digest(name), name, (self.frontend / name).read_bytes(),
                            bucket=outputs["FrontendBucket"],
                            content_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
                            cache_control="no-cache" if name == "index.html" else "public,max-age=31536000,immutable")
        cloudfront = self.target.session.client("cloudfront")
        request = {"distribution": outputs["DistributionId"], "files_digest": digest(files)}

        def read(record):
            parameters = {"DistributionId": outputs["DistributionId"]}
            for _ in range(100):
                listing = cloudfront.list_invalidations(**parameters)["InvalidationList"]
                for item in listing.get("Items", []):
                    current = cloudfront.get_invalidation(
                        DistributionId=outputs["DistributionId"], Id=item["Id"])["Invalidation"]
                    if current["InvalidationBatch"]["CallerReference"] == record["request_token"]:
                        return {"id": current["Id"]}
                if not listing["IsTruncated"]:
                    return None
                parameters["Marker"] = listing["NextMarker"]
            raise RuntimeError("Incomplete CloudFront invalidation inventory")

        def write(token):
            result = cloudfront.create_invalidation(DistributionId=outputs["DistributionId"],
                InvalidationBatch={"Paths": {"Quantity": 1, "Items": ["/*"]}, "CallerReference": token})
            return {"id": result["Invalidation"]["Id"]}

        receipt = Journal(self.target).run("install-invalidation", request, write, read)
        for _ in range(120):
            value = cloudfront.get_invalidation(DistributionId=outputs["DistributionId"], Id=receipt["id"])
            if value["Invalidation"]["Status"] == "Completed":
                self.target.save("frontendPublication", {"invalidationId": receipt["id"],
                                                         "indexSha256": files["index.html"]})
                return
            print("CloudFront publication pending", flush=True)
            time.sleep(5)
        raise RuntimeError("Publication pending; resume using the saved installation state")

    def audit(self):
        from scripts.mcp_onboarding_audit import audit
        audit(self.config.state, existing=True, output=self.config.evidence / "security-after.json")
        if self.config.user_oauth:
            self.user_gateway().audit()
        if self.config.package_uploads:
            self.facade("audit")

    def run_stage(self, name):
        self.verify_completed("source")
        if STAGES.index(name) > STAGES.index("build"):
            self.verify_build()
        getattr(self, name.replace("-", "_"))()

    def deploy(self):
        run_steps(self.target, {name: lambda name=name: self.run_stage(name) for name in stage_names(self.config)},
                  verify_completed=self.verify_completed)


def status(config):
    if not config.state.exists():
        return {"target": config.binding, "status": "NOT_STARTED", "user_acceptance": "NOT_RUN"}
    saved = json_file(config.state)
    if saved.get("target") != config.binding or saved.get("installation", {}).get("configuration") != config.intent():
        raise ValueError("Saved installation target or configuration differs")
    return {"target": config.binding, "verification": "Saved receipts; use audit for current AWS checks",
            "steps": saved["installation"]["steps"], "user_acceptance": saved["installation"]["user_acceptance"],
            "application": {key: value for key, value in saved.get("app", {}).get("outputs", {}).items()
                            if key in ("ApplicationOrigin", "UserPoolId")},
            "state": str(config.state), "evidence": str(config.evidence)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "deploy", "status", "audit"))
    parser.add_argument("--config", required=True, type=Path, help="Operator account, profile, region and feature options")
    args = parser.parse_args(argv)
    config = load_configuration(args.config)
    if args.action == "plan":
        print(json.dumps({"configuration": config.intent(), "state": str(config.state),
                          "evidence": str(config.evidence), "stages": stage_names(config),
                          "aws_writes": False, "user_acceptance": "NOT_RUN"}, indent=2))
        return
    if args.action == "status":
        print(json.dumps(status(config), indent=2))
        return
    if args.action == "audit" and not config.state.exists():
        raise ValueError("There is no installation to audit")
    pipeline = Pipeline(config)
    getattr(pipeline, args.action)()
    print(json.dumps(status(config), indent=2))


if __name__ == "__main__":
    from botocore.exceptions import ClientError
    try:
        main()
    except ClientError as error:
        print("AWS operation failed: " + error.response["Error"]["Code"], file=sys.stderr)
        sys.exit(1)
