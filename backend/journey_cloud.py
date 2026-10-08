"""AWS lifecycle operations for the published self-service Foundation Harness."""
import json
import math

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from foundation_harness.config import canonical, digest
from .network_policy import networks_equivalent, validate_network


class JourneyCloud:
    mode = "live"

    def __init__(self, settings, session=None):
        self.settings = settings
        self.session = session or boto3.Session(region_name=settings["region"])
        sdk = Config(connect_timeout=5, read_timeout=210, retries={"total_max_attempts": 1})
        self.control = self.session.client("bedrock-agentcore-control", config=sdk)
        # Status polls run inside the worker's bounded in-process wait drain
        # (serverless.STATUS_WAIT_STEP_BUDGET_MS): a poll slower than 30s is
        # treated as "not READY yet", never as a step that may outlive the
        # Lambda invocation.
        self.poll_control = self.session.client("bedrock-agentcore-control",
            config=Config(connect_timeout=5, read_timeout=30, retries={"total_max_attempts": 1}))
        self.data = self.session.client("bedrock-agentcore", config=sdk)
        self.s3 = self.session.client("s3", config=sdk)

    def write(self, key, value):
        data = canonical(value)
        try:
            response = self.s3.put_object(Bucket=self.settings["bucket"], Key=key, Body=data,
                                          ContentType="application/json", ServerSideEncryption="AES256",
                                          IfNoneMatch="*")
            version = response["VersionId"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in ("PreconditionFailed", "412"):
                raise
            response = self.s3.get_object(Bucket=self.settings["bucket"], Key=key)
            try:
                if response["Body"].read(len(data) + 1) != data:
                    raise ValueError("Immutable artifact already contains different content")
            finally:
                response["Body"].close()
            version = response["VersionId"]
        if not version or version == "null":
            raise ValueError("Versioned storage is required")
        return {"bucket": self.settings["bucket"], "key": key, "version_id": version, "digest": digest(value)}

    def find_agent_runtimes(self, agent_id, workspace, plans):
        from .journey_cleanup_cloud import find_runtimes
        return find_runtimes(self, agent_id, workspace, plans)

    def delete_runtime(self, binding):
        from .journey_cleanup_cloud import delete_runtime
        return delete_runtime(self, binding)

    def purge_agent_objects(self, entry):
        from .journey_cleanup_cloud import purge_objects
        return purge_objects(self, entry)

    def delete_agent_log_streams(self, agent_id, versions):
        from .journey_cleanup_cloud import delete_streams
        return delete_streams(self, agent_id, versions)

    def read(self, location):
        response = self.s3.get_object(Bucket=self.settings["bucket"], Key=location["key"],
                                      VersionId=location["version_id"])
        try:
            raw = response["Body"].read(350001)
        finally:
            response["Body"].close()
        if len(raw) > 350000:
            raise ValueError("Evidence exceeds the read limit")
        value = json.loads(raw)
        if response["VersionId"] != location["version_id"] or digest(value) != location["digest"]:
            raise ValueError("Immutable evidence binding mismatch")
        return value

    def create(self, manifest, token):
        network = validate_network(self.settings["network"])
        location = self.write("journey/manifests/" + digest(manifest) + ".json", manifest)
        artifact = manifest["artifact"]
        request = {
            "agentRuntimeName": "gab_journey_" + token[:24],
            "agentRuntimeArtifact": {"codeConfiguration": {
                "code": {"s3": {"bucket": artifact["bucket"], "prefix": artifact["key"],
                               "versionId": artifact["version_id"]}},
                "runtime": "PYTHON_3_13", "entryPoint": ["main.py"]}},
            "roleArn": self.settings["runtime_role"],
            "networkConfiguration": network,
            "protocolConfiguration": {"serverProtocol": "HTTP"},
            "lifecycleConfiguration": {"idleRuntimeSessionTimeout": 60, "maxLifetime": 900},
            "environmentVariables": {"JOURNEY_MANIFEST": json.dumps(location, separators=(",", ":"))},
            "clientToken": token,
            "tags": {"project": "governed-agent-builder", "journey": "create-agent",
                     "agent": manifest["agent_id"], "workspace": manifest["workspace"],
                     "auto-delete": "no"},
        }
        try:
            response = self.control.create_agent_runtime(**request)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConflictException":
                raise
            binding = self._reconcile_created_runtime(request, location, manifest)
            if binding is None:
                raise
            return binding
        binding = {"id": response["agentRuntimeId"], "arn": response["agentRuntimeArn"],
                   "version": response["agentRuntimeVersion"], "manifest": location}
        if manifest.get("gateway_force_auth_v1") is True:
            binding["gateway_force_auth_v1"] = True
        if not binding["arn"].startswith(f"arn:aws:bedrock-agentcore:{self.settings['region']}:{self.settings['account']}:runtime/gab_journey_"):
            raise ValueError("Runtime target account or resource prefix mismatch")
        return binding

    def _reconcile_created_runtime(self, request, location, manifest=None):
        matches = []
        token = None
        while True:
            page = self.control.list_agent_runtimes(**({"nextToken": token} if token else {}))
            matches.extend(runtime for runtime in page.get("agentRuntimes", [])
                           if runtime["agentRuntimeName"] == request["agentRuntimeName"])
            token = page.get("nextToken")
            if not token:
                break
        if not matches:
            return None
        if len(matches) != 1:
            raise ValueError("Existing named Runtime is ambiguous; operator review required")
        runtime = matches[0]
        runtime_id = runtime["agentRuntimeId"]
        arn = f"arn:aws:bedrock-agentcore:{self.settings['region']}:{self.settings['account']}:runtime/{runtime_id}"
        if (not runtime_id.startswith(request["agentRuntimeName"] + "-")
                or runtime["agentRuntimeArn"] != arn):
            raise ValueError("Existing named Runtime is outside this account or namespace; operator review required")
        version = runtime["agentRuntimeVersion"]
        native = self.control.get_agent_runtime(agentRuntimeId=runtime_id, agentRuntimeVersion=version)
        tags = self.control.list_tags_for_resource(resourceArn=arn)["tags"]
        expected_fields = ("agentRuntimeArtifact", "roleArn",
                           "protocolConfiguration", "lifecycleConfiguration", "environmentVariables")
        if (native.get("agentRuntimeId") != runtime_id or native.get("agentRuntimeArn") != arn
                or native.get("agentRuntimeVersion") != version
                or native.get("agentRuntimeName") != request["agentRuntimeName"]
                or native.get("status") not in ("CREATING", "UPDATING", "READY")
                or not networks_equivalent(native.get("networkConfiguration"), request["networkConfiguration"])
                or any(native.get(field) != request[field] for field in expected_fields)
                or any(tags.get(key) != value for key, value in request["tags"].items())):
            raise ValueError("Existing named Runtime differs from this agent deployment; operator review required")
        return {"id": runtime_id, "arn": arn, "version": version, "manifest": location,
                **({"gateway_force_auth_v1": True} if (manifest or {}).get("gateway_force_auth_v1") is True else {})}

    def ready(self, binding, poll=False):
        # poll=True bounds each control call to the poll client's 30s read
        # timeout and reports a timed-out status read as "not READY yet" so a
        # drained worker step always fits its 250s budget. Non-poll callers
        # keep the full 210s single-attempt behavior unchanged.
        from botocore.exceptions import ConnectTimeoutError, ReadTimeoutError
        control = self.poll_control if poll else self.control
        try:
            result = control.get_agent_runtime(agentRuntimeId=binding["id"], agentRuntimeVersion=binding["version"])
        except (ConnectTimeoutError, ReadTimeoutError):
            if poll:
                return False
            raise
        if result["agentRuntimeArn"] != binding["arn"] or result["agentRuntimeVersion"] != binding["version"]:
            raise ValueError("Runtime version binding mismatch")
        if json.loads(result.get("environmentVariables", {}).get("JOURNEY_MANIFEST", "{}")) != binding["manifest"]:
            raise ValueError("Runtime manifest changed")
        if result["status"] in ("CREATE_FAILED", "UPDATE_FAILED", "DELETING"):
            raise ValueError("AgentCore could not prepare this Runtime")
        if result["status"] != "READY":
            return False
        try:
            endpoint = control.get_agent_runtime_endpoint(agentRuntimeId=binding["id"], endpointName="DEFAULT")
        except (ConnectTimeoutError, ReadTimeoutError):
            if poll:
                return False
            raise
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ResourceNotFoundException":
                return False
            raise
        return (endpoint["status"] == "READY" and endpoint["agentRuntimeArn"] == binding["arn"]
                and endpoint["liveVersion"] == binding["version"]
                and endpoint.get("targetVersion", binding["version"]) == binding["version"])

    def provision_runtime_logs(self, binding):
        from .journey_runtime_logs import provision
        return provision(self, binding)

    def invoke(self, binding, definition, text, request_id, history=None, user_token=None, force_gateway_auth=False):
        if force_gateway_auth and binding.get("gateway_force_auth_v1") is not True:
            raise ValueError("This Runtime does not support forced Gateway authorization")
        if not self.ready(binding):
            raise ValueError("Runtime endpoint is not ready for the deployed version")
        response = self.data.invoke_agent_runtime(
            agentRuntimeArn=binding["arn"], qualifier="DEFAULT", runtimeSessionId="gab-" + request_id,
            payload=canonical({"input": text, "request_id": request_id,
                               **({"history": history} if history else {}),
                               **({"user_token": user_token} if user_token else {}),
                               **({"force_gateway_auth": True} if force_gateway_auth else {})}), contentType="application/json",
            accept="application/json")
        stream = response["response"]
        try:
            raw = stream.read(350001)
        finally:
            stream.close()
        if len(raw) > 350000:
            raise ValueError("Runtime returned excessive evidence")
        receipt = json.loads(raw)
        if receipt.get("status") == "AUTHORIZATION_REQUIRED":
            fields = {"status", "authorization", "definition_digest", "session_id"}
            count = receipt.get("completed_tool_calls")
            if (set(receipt) not in (fields, fields | {"completed_tool_calls"})
                    or receipt["definition_digest"] != definition["digest"]
                    or receipt["session_id"] != "gab-" + request_id
                    or not isinstance(receipt["authorization"], dict)
                    or ("completed_tool_calls" in receipt and count is not None
                        and (type(count) is not int or not 0 <= count <= 6))):
                raise ValueError("Authorization challenge does not match this agent invocation")
            return receipt
        self.validate_receipt(receipt, definition, request_id)
        stored = self.read(receipt["evidence"])
        if stored != {key: value for key, value in receipt.items() if key != "evidence"}:
            raise ValueError("Runtime output does not match its persisted trace evidence")
        return receipt

    def recover_invocation(self, definition, request_id):
        key = "journey/evidence/" + definition["digest"] + "/" + request_id + ".json"
        try:
            response = self.s3.get_object(Bucket=self.settings["bucket"], Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None
            raise
        try:
            raw = response["Body"].read(350001)
        finally:
            response["Body"].close()
        if len(raw) > 350000:
            raise ValueError("Recovered evidence exceeds the read limit")
        receipt = json.loads(raw)
        self.validate_receipt(receipt, definition, request_id)
        return {**receipt, "evidence": {"key": key, "version_id": response["VersionId"], "digest": digest(receipt)}}

    @staticmethod
    def validate_receipt(receipt, definition, request_id):
        if (receipt.get("status") != "SUCCEEDED" or not receipt.get("output", "").strip()
                or receipt.get("definition_digest") != definition["digest"]
                or receipt.get("model_id") != definition["resolved_model_id"]
                or receipt.get("session_id") != "gab-" + request_id
                or not receipt.get("spans")
                or any(span["traceId"] != receipt["trace_id"] for span in receipt["spans"])):
            raise ValueError("Execution evidence does not match this agent version and session")

    def evaluate(self, definition, case, receipt, evaluation_id):
        evaluator_id = self.settings.get("evaluator_id", "Builtin.Correctness")
        native = self.control.get_evaluator(evaluatorId=evaluator_id)
        if native.get("status") != "ACTIVE" or native.get("level") != "TRACE":
            raise ValueError("An active TRACE evaluator is required")
        request = {"evaluatorId": evaluator_id, "evaluationInput": {"sessionSpans": receipt["spans"]},
                   "evaluationTarget": {"traceIds": [receipt["trace_id"]]}}
        reference = {"context": {"spanContext": {"sessionId": receipt["session_id"], "traceId": receipt["trace_id"]}}}
        if case.get("expected_response"):
            reference["expectedResponse"] = {"text": case["expected_response"]}
        if case.get("assertions"):
            reference["assertions"] = [{"text": text} for text in case["assertions"]]
        if len(reference) > 1:
            request["evaluationReferenceInputs"] = [reference]
        response = self.data.evaluate(**request)
        location = self.write("journey/evaluations/" + evaluation_id + "/" + case["id"] + ".json",
                              {"request": request, "response": response,
                               "definition_digest": definition["digest"], "case_id": case["id"]})
        results = response.get("evaluationResults", [])
        if len(results) != 1:
            raise ValueError("AgentCore did not return exactly one evaluation result")
        result = results[0]
        context = result.get("context", {}).get("spanContext", {})
        if (result.get("evaluatorId") != evaluator_id or result.get("errorCode") or result.get("errorMessage")
                or context.get("sessionId") != receipt["session_id"]
                or context.get("traceId") != receipt["trace_id"]):
            raise ValueError("AgentCore evaluation result is incomplete or belongs to another trace")
        value = result.get("value")
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("AgentCore returned no finite numeric score")
        if not 0 <= value <= 1:
            raise ValueError("Evaluator returned a score outside its configured 0–1 scale")
        ignored = result.get("ignoredReferenceInputFields", [])
        return {"score": value, "explanation": result.get("explanation", ""),
                "label": result.get("label"), "ignored_reference_fields": ignored,
                "evaluator_id": evaluator_id, "request_id": response.get("ResponseMetadata", {}).get("RequestId"),
                "evidence": location}
