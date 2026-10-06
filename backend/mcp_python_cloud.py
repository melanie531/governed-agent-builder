"""Versioned Python packages and IAM-only MCP Runtimes; no build-time execution."""
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from uuid import uuid4
import zipfile

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from foundation_harness.journey_mcp import decode_rpc_response
from .mcp_onboarding import endpoint_origin

TOKEN_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Snowflake-Token"
ACCESS_TOKEN_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Access-Token"
MAX_ZIP = 64 * 1024 * 1024


class McpDiscoveryError(ValueError):
    """The deployed server did not complete the bounded MCP discovery exchange."""


def configuration(value, settings):
    legacy = {"bundle_name", "artifact", "runtime_role", "runtime_prefix", "deployment_prefix", "facade_url"}
    standalone = {"package_bucket", "runtime_role", "runtime_prefix", "deployment_prefix"}
    if not isinstance(value, dict) or set(value) not in (legacy, standalone, standalone | {"facade_url"}):
        raise ValueError("Python MCP configuration is missing")
    if "artifact" in value:
        artifact = value["artifact"]
        if (set(artifact) != {"bucket", "key", "version_id", "digest"}
                or not re.fullmatch(r"mcp/python/[A-Za-z0-9/_-]+\.zip", artifact["key"])
                or not artifact["version_id"] or artifact["version_id"] == "null"
                or not re.fullmatch(r"[a-f0-9]{64}", artifact["digest"])
                or not isinstance(value["bundle_name"], str) or not 1 <= len(value["bundle_name"]) <= 100):
            raise ValueError("Python MCP bundle binding is invalid")
    if (not re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,62}", artifact_bucket(value))
            or not re.fullmatch(re.escape(f"arn:aws:iam::{settings['account']}:role/") + r"[A-Za-z0-9+=,.@_-]+", value["runtime_role"])
            or not re.fullmatch(r"[a-z][a-z0-9_]{2,19}", value["runtime_prefix"])
            or not re.fullmatch(r"[a-z][a-z0-9-]{2,45}", value["deployment_prefix"])
            or ("facade_url" in value and endpoint_origin(value["facade_url"]) != value["facade_url"])):
        raise ValueError("Python MCP deployment binding is invalid")
    return value


def artifact_bucket(config):
    return config["artifact"]["bucket"] if "artifact" in config else config["package_bucket"]


def package_binding(state, config):
    return ({"package-digest": state["source_digest"]} if state.get("upload_type") == "package"
            else {"bundle-digest": config["artifact"]["digest"]})


def package(base, expected_digest, source, output):
    if hashlib.sha256(Path(base).read_bytes()).hexdigest() != expected_digest:
        raise ValueError("Approved dependency bundle digest changed")
    with zipfile.ZipFile(base) as archive, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as result:
        entries = archive.infolist()
        if (len({e.filename for e in entries}) != len(entries) or sum(e.file_size for e in entries) > 256 * 1024 * 1024
                or any(e.filename.startswith("/") or ".." in PurePosixPath(e.filename).parts
                       or (e.external_attr >> 16) & 0o170000 == 0o120000 for e in entries)):
            raise ValueError("Approved dependency bundle layout is invalid")
        for entry in entries:
            if entry.filename == "main.py":
                continue
            with archive.open(entry) as reader, result.open(entry, "w") as writer:
                shutil.copyfileobj(reader, writer)
        entry = zipfile.ZipInfo("main.py", (1980, 1, 1, 0, 0, 0))
        entry.compress_type, entry.external_attr = zipfile.ZIP_DEFLATED, 0o100644 << 16
        result.writestr(entry, source.encode())


class PythonCloud:
    def __init__(self, settings, session=None):
        self.settings = settings
        self.session = session or boto3.Session(region_name=settings["region"])
        sdk = Config(retries={"total_max_attempts": 1}, connect_timeout=5, read_timeout=60)
        self.control = self.session.client("bedrock-agentcore-control", config=sdk)
        self.data = self.session.client("bedrock-agentcore", config=sdk)
        self.s3 = self.session.client("s3", config=sdk)
        self.logs = self.session.client("logs", config=sdk)

    @staticmethod
    def tags(state, config):
        return {"auto-delete": "no", "project": "governed-agent-builder",
            "deployment": config["deployment_prefix"], "python-mcp": state["id"],
            "source-digest": state["source_digest"], **package_binding(state, config)}

    @staticmethod
    def object_key(state):
        return "mcp/python/" + state["id"] + "/runtime.zip"

    def runtime_request(self, state, config):
        artifact = state["artifact"]
        request = {
            "agentRuntimeName": state["runtime_name"], "clientToken": "python-" + state["id"],
            "agentRuntimeArtifact": {"codeConfiguration": {"code": {"s3": {
                "bucket": artifact["bucket"], "prefix": artifact["key"], "versionId": artifact["version_id"]}},
                "runtime": "PYTHON_3_13", "entryPoint": ["main.py"]}},
            "roleArn": config["runtime_role"], "networkConfiguration": {"networkMode": "PUBLIC"},
            "protocolConfiguration": {"serverProtocol": "MCP"},
            "lifecycleConfiguration": {"idleRuntimeSessionTimeout": 60, "maxLifetime": 900},
            "tags": self.tags(state, config),
        }
        if state.get("connection_mode", "SNOWFLAKE_OAUTH") == "SNOWFLAKE_OAUTH":
            request["requestHeaderConfiguration"] = {"requestHeaderAllowlist": [TOKEN_HEADER]}
            request["environmentVariables"] = {"SNOWFLAKE_ACCOUNT": state["snowflake_account"],
                "SNOWFLAKE_ROLE": state["snowflake_role"], "SNOWFLAKE_WAREHOUSE": state["warehouse"],
                "SNOWFLAKE_AUTH_SOURCE": "gateway"}
        elif state.get("connection_mode") == "PACKAGE" and state.get("bearer_validation_tool"):
            request["requestHeaderConfiguration"] = {"requestHeaderAllowlist": [ACCESS_TOKEN_HEADER]}
        return request

    def runtime(self, state, config, *, retiring=False):
        matches, token = [], None
        for _ in range(30):
            page = self.control.list_agent_runtimes(**({"nextToken": token} if token else {}))
            matches.extend(r for r in page.get("agentRuntimes", []) if r["agentRuntimeName"] == state["runtime_name"])
            token = page.get("nextToken")
            if not token:
                break
        else:
            raise ValueError("Runtime inventory is incomplete")
        if not matches:
            return None
        if len(matches) != 1:
            raise ValueError("Python Runtime identity is ambiguous")
        native_id = matches[0]["agentRuntimeId"]
        arn = f"arn:aws:bedrock-agentcore:{self.settings['region']}:{self.settings['account']}:runtime/{native_id}"
        if (not native_id.startswith(state["runtime_name"] + "-") or matches[0]["agentRuntimeArn"] != arn
                or matches[0]["agentRuntimeVersion"] != "1"
                or state.get("runtime_arn", arn) != arn or state.get("runtime_version", "1") != "1"):
            raise ValueError("Python Runtime identity or version changed")
        native = self.control.get_agent_runtime(agentRuntimeId=native_id, agentRuntimeVersion="1")
        request = self.runtime_request(state, config)
        if (native.get("agentRuntimeArn") != arn or native.get("agentRuntimeId") != native_id
                or native.get("agentRuntimeVersion") != "1" or native.get("authorizerConfiguration")
                or state.get("connection_mode") in ("IAM", "PACKAGE") and native.get("environmentVariables")
                or not request.get("requestHeaderConfiguration") and native.get("requestHeaderConfiguration")
                or any(native.get(k) != v for k, v in request.items() if k not in ("clientToken", "tags"))
                or native["status"] not in (("CREATING", "READY", "DELETING", "CREATE_FAILED", "UPDATE_FAILED", "DELETE_FAILED")
                                           if retiring else ("CREATING", "READY"))):
            raise ValueError("Python Runtime deployment binding changed")
        tags = self.control.list_tags_for_resource(resourceArn=arn)["tags"]
        if any(tags.get(k) != v for k, v in request["tags"].items()):
            raise ValueError("Python Runtime ownership or retention tags changed")
        if retiring and native["status"] in ("DELETING", "CREATE_FAILED", "UPDATE_FAILED", "DELETE_FAILED"):
            return {"runtime_id": native_id, "runtime_arn": arn, "runtime_version": "1",
                    "deleting": native["status"] == "DELETING"}
        if native["status"] != "READY":
            return {"pending": True}
        endpoint = self.control.get_agent_runtime_endpoint(agentRuntimeId=native_id, endpointName="DEFAULT")
        if endpoint["agentRuntimeArn"] != arn or endpoint["liveVersion"] != "1" or endpoint.get("targetVersion", "1") != "1":
            raise ValueError("Python Runtime endpoint version changed")
        if endpoint["status"] != "READY":
            return {"pending": True}
        identity = f"arn:aws:bedrock-agentcore:{self.settings['region']}:{self.settings['account']}:workload-identity-directory/default/workload-identity/{native_id}"
        if (endpoint["agentRuntimeEndpointArn"] != arn + "/runtime-endpoint/DEFAULT"
                or native.get("workloadIdentityDetails", {}).get("workloadIdentityArn") != identity):
            raise ValueError("Python Runtime generated resource binding changed")
        return {"runtime_id": native_id, "runtime_arn": arn, "runtime_version": "1"}

    def generated_tags(self, runtime, required):
        identity = f"arn:aws:bedrock-agentcore:{self.settings['region']}:{self.settings['account']}:workload-identity-directory/default/workload-identity/{runtime['runtime_id']}"
        values = {arn: self.control.list_tags_for_resource(resourceArn=arn)["tags"]
                  for arn in (runtime["runtime_arn"] + "/runtime-endpoint/DEFAULT", identity)}
        if any(k in tags and tags[k] != v for tags in values.values() for k, v in required.items()):
            raise ValueError("Python Runtime generated resource ownership changed")
        return values

    def read(self, stage, state, config):
        if stage == "package":
            try:
                value = self.s3.head_object(Bucket=artifact_bucket(config), Key=self.object_key(state),
                    ExpectedBucketOwner=self.settings["account"], ChecksumMode="ENABLED")
            except ClientError as error:
                if error.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                    return None
                if error.response["Error"]["Code"] in ("403", "AccessDenied"):
                    # S3 hides missing keys from roles without unrestricted bucket listing.
                    key = self.object_key(state)
                    page = self.s3.list_objects_v2(Bucket=artifact_bucket(config),
                        Prefix=key, MaxKeys=1, ExpectedBucketOwner=self.settings["account"])
                    if not any(item["Key"] == key for item in page.get("Contents", [])):
                        return None
                raise
            metadata = value.get("Metadata", {})
            sha = metadata.get("sha256", "")
            if (metadata.get("source-digest") != state["source_digest"]
                    or any(metadata.get(k) != v for k, v in package_binding(state, config).items())
                    or state.get("upload_type") == "package" and sha != state["source_digest"]
                    or not re.fullmatch(r"[a-f0-9]{64}", sha)
                    or value.get("ChecksumSHA256") != base64.b64encode(bytes.fromhex(sha)).decode()
                    or not value.get("VersionId") or value["VersionId"] == "null"
                    or not 0 < value["ContentLength"] <= MAX_ZIP or value.get("ServerSideEncryption") != "AES256"):
                raise ValueError("Python package binding changed")
            receipt = {"artifact": {"bucket": artifact_bucket(config), "key": self.object_key(state),
                "version_id": value["VersionId"], "digest": sha}}
            if state.get("connection_mode") == "PACKAGE":
                tool = metadata.get("bearer-validation-tool")
                if tool is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", tool):
                    raise ValueError("Package authentication binding changed")
                if tool and not config.get("facade_url"):
                    raise ValueError("Package bearer authentication is not configured")
                receipt["bearer_validation_tool"] = tool
            return receipt
        runtime = self.runtime(state, config)
        if stage == "runtime" or runtime is None or runtime.get("pending"):
            return runtime
        if stage != "logs":
            raise ValueError("Unsupported Python resource stage")
        name = "/aws/bedrock-agentcore/runtimes/" + runtime["runtime_id"] + "-DEFAULT"
        page = self.logs.describe_log_groups(logGroupNamePrefix=name)
        group = next((g for g in page["logGroups"] if g["logGroupName"] == name), None)
        if group is None and page.get("nextToken"):
            raise ValueError("Python log inventory is incomplete")
        if not group:
            return None
        tags = self.logs.list_tags_for_resource(resourceArn=group["arn"].removesuffix(":*"))["tags"]
        if any(k in tags and tags[k] != v for k, v in self.tags(state, config).items()):
            raise ValueError("Python log ownership changed")
        if group.get("retentionInDays") != 14 or any(tags.get(k) != v for k, v in self.tags(state, config).items()):
            return None
        required = self.tags(state, config)
        generated = self.generated_tags(runtime, required)
        if any(tags.get(k) != v for tags in generated.values() for k, v in required.items()):
            return None
        return {"log_group": name}

    def write(self, stage, state, config, source=None):
        if stage == "runtime":
            return self.control.create_agent_runtime(**self.runtime_request(state, config))
        if stage == "logs":
            runtime = self.runtime(state, config)
            if not runtime or runtime.get("pending"):
                raise ValueError("Python Runtime is not ready")
            required = self.tags(state, config)
            generated = self.generated_tags(runtime, required)
            from .journey_runtime_logs import provision
            receipt = provision(self, {"id": runtime["runtime_id"], "arn": runtime["runtime_arn"]},
                runtime_prefix=config["runtime_prefix"] + "_", required_tags=required)
            for arn, tags in generated.items():
                if any(tags.get(k) != v for k, v in required.items()):
                    self.control.tag_resource(resourceArn=arn, tags=required)
            return receipt
        if stage == "package" and state.get("upload_type") == "package":
            return self.write_complete_package(state, config)
        if stage != "package" or not isinstance(source, str) or hashlib.sha256(source.encode()).hexdigest() != state["source_digest"]:
            raise ValueError("Python source binding changed")
        artifact = config["artifact"]
        response = self.s3.get_object(Bucket=artifact["bucket"], Key=artifact["key"],
            VersionId=artifact["version_id"], ExpectedBucketOwner=self.settings["account"])
        with tempfile.TemporaryDirectory(prefix="studio-mcp-") as directory:
            base, output = Path(directory) / "base.zip", Path(directory) / "runtime.zip"
            try:
                if response.get("VersionId") != artifact["version_id"] or response["ContentLength"] > MAX_ZIP:
                    raise ValueError("Approved Python bundle version or size changed")
                base.write_bytes(response["Body"].read(MAX_ZIP + 1))
            finally:
                response["Body"].close()
            if base.stat().st_size > MAX_ZIP:
                raise ValueError("Approved Python bundle exceeds the size limit")
            package(base, artifact["digest"], source, output)
            data = output.read_bytes()
            if len(data) > MAX_ZIP:
                raise ValueError("Python package exceeds the size limit")
            sha = hashlib.sha256(data)
            return self.s3.put_object(Bucket=artifact["bucket"], Key=self.object_key(state), Body=data,
                ExpectedBucketOwner=self.settings["account"], IfNoneMatch="*", ServerSideEncryption="AES256",
                ContentType="application/zip", ChecksumSHA256=base64.b64encode(sha.digest()).decode(),
                Metadata={"source-digest": state["source_digest"], "bundle-digest": artifact["digest"], "sha256": sha.hexdigest()},
                Tagging="auto-delete=no")

    def write_complete_package(self, state, config):
        from .mcp_package import InvalidPackage, PackageCloud, PART_BYTES, validate_archive
        parts = state["source_parts"]
        if not parts or len(parts) > MAX_ZIP // PART_BYTES:
            raise InvalidPackage("Package part count is invalid")
        bucket = artifact_bucket(config)
        with tempfile.TemporaryDirectory(prefix="studio-package-") as directory:
            output = Path(directory) / "runtime.zip"
            with output.open("wb") as writer:
                for index, part in enumerate(parts):
                    if (part["key"] != PackageCloud.part_key(state["id"], index)
                            or not 0 < part["size"] <= PART_BYTES or not part["version_id"]
                            or part["version_id"] == "null"):
                        raise InvalidPackage("Package part identity is invalid")
                    response = self.s3.get_object(Bucket=bucket, Key=part["key"], VersionId=part["version_id"],
                        ExpectedBucketOwner=self.settings["account"], ChecksumMode="ENABLED")
                    try:
                        data = response["Body"].read(PART_BYTES + 1)
                    finally:
                        response["Body"].close()
                    if (len(data) != part["size"] or response.get("ContentLength") != part["size"]
                            or response.get("VersionId") != part["version_id"]
                            or hashlib.sha256(data).hexdigest() != part["digest"]
                            or response.get("Metadata") != {"upload-id": state["id"],
                                "archive-digest": state["source_digest"], "sha256": part["digest"]}
                            or response.get("ServerSideEncryption") != "AES256"
                            or output.stat().st_size + len(data) > MAX_ZIP):
                        raise InvalidPackage("Package part checksum or binding changed")
                    writer.write(data)
            if output.stat().st_size != state["size"]:
                raise InvalidPackage("Package size changed")
            contract = validate_archive(output, state["source_digest"])
            sha = state["source_digest"]
            metadata = {"source-digest": sha, "package-digest": sha, "sha256": sha}
            if state.get("connection_mode") == "PACKAGE" and contract.get("bearer_validation_tool"):
                metadata["bearer-validation-tool"] = contract["bearer_validation_tool"]
            with output.open("rb") as data:
                return self.s3.put_object(Bucket=bucket, Key=self.object_key(state), Body=data,
                    ExpectedBucketOwner=self.settings["account"], IfNoneMatch="*", ServerSideEncryption="AES256",
                    ContentType="application/zip", ChecksumAlgorithm="SHA256",
                    ChecksumSHA256=base64.b64encode(bytes.fromhex(sha)).decode(),
                    Metadata=metadata, Tagging="auto-delete=no")

    def retirement_plan(self, state, config):
        from .mcp_deployment_cloud import inventory
        return inventory(self, state, config)

    def retirement_read(self, resource, state, config):
        from .mcp_deployment_cloud import read
        return read(self, resource, state, config)

    def retirement_write(self, resource, state, config):
        from .mcp_deployment_cloud import write
        return write(self, resource, state, config)

    def discover(self, state, config):
        if self.runtime(state, config) != {k: state[k] for k in ("runtime_id", "runtime_arn", "runtime_version")}:
            raise ValueError("Python Runtime is not ready at its bound version")
        def rpc(method, params, notification=False):
            request = {"jsonrpc": "2.0", "method": method, "params": params}
            if not notification:
                request["id"] = uuid4().hex
            response = self.data.invoke_agent_runtime(agentRuntimeArn=state["runtime_arn"], qualifier="DEFAULT",
                runtimeSessionId="python-mcp-" + state["id"], contentType="application/json",
                accept="application/json, text/event-stream", payload=json.dumps(request).encode())
            try:
                raw = response["response"].read(512 * 1024 + 1)
            finally:
                response["response"].close()
            if len(raw) > 512 * 1024 or not 200 <= response.get("statusCode", 200) < 300:
                raise McpDiscoveryError("Python MCP discovery failed or exceeded its size limit")
            if notification:
                return {}
            try:
                value = decode_rpc_response(raw, response.get("contentType", ""), request["id"])
            except ValueError:
                raise McpDiscoveryError("Python MCP did not return a valid protocol response") from None
            if value.get("id") != request["id"] or "error" in value or not isinstance(value.get("result"), dict):
                raise McpDiscoveryError("Python MCP protocol request was rejected")
            return value["result"]
        initialized = rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "agent-studio", "version": "1.0"}})
        if "tools" not in initialized.get("capabilities", {}):
            raise McpDiscoveryError("Python MCP does not advertise tools")
        rpc("notifications/initialized", {}, notification=True)
        tools, cursor = [], None
        for _ in range(10):
            page = rpc("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(page.get("tools", []))
            cursor = page.get("nextCursor")
            if not cursor:
                return tools
        raise McpDiscoveryError("Python MCP tool inventory is incomplete")
