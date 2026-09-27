"""AWS lifecycle operations for the published self-service Foundation Harness."""
import json
import math

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from foundation_harness.config import canonical, digest


class JourneyCloud:
    mode = "live"

    def __init__(self, settings, session=None):
        self.settings = settings
        self.session = session or boto3.Session(region_name=settings["region"])
        sdk = Config(connect_timeout=5, read_timeout=210, retries={"total_max_attempts": 1})
        self.control = self.session.client("bedrock-agentcore-control", config=sdk)
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
        from foundation_harness.alpr_exchange import is_alpr_tool
        if any(is_alpr_tool(tool["name"]) for tool in manifest["tools"]):
            # Host registers an exact deployment after AWS proof. ALPR never
            # creates another Runtime with the shared Journey execution role.
            binding = self.settings.get("alpr_deployments", {}).get(digest(manifest))
            if not binding or not self.ready(binding):
                raise ValueError("An exact registered ALPR deployment is required")
            return binding
        location = self.write("journey/manifests/" + digest(manifest) + ".json", manifest)
        artifact = manifest["artifact"]
        response = self.control.create_agent_runtime(
            agentRuntimeName="gab_journey_" + token[:24],
            agentRuntimeArtifact={"codeConfiguration": {
                "code": {"s3": {"bucket": artifact["bucket"], "prefix": artifact["key"],
                               "versionId": artifact["version_id"]}},
                "runtime": "PYTHON_3_13", "entryPoint": ["main.py"]}},
            roleArn=self.settings["runtime_role"],
            networkConfiguration=self.settings["network"],
            protocolConfiguration={"serverProtocol": "HTTP"},
            lifecycleConfiguration={"idleRuntimeSessionTimeout": 60, "maxLifetime": 900},
            environmentVariables={"JOURNEY_MANIFEST": json.dumps(location, separators=(",", ":"))},
            clientToken=token,
            tags={"project": "governed-agent-builder", "journey": "create-agent",
                  "agent": manifest["agent_id"], "workspace": manifest["workspace"]})
        binding = {"id": response["agentRuntimeId"], "arn": response["agentRuntimeArn"],
                   "version": response["agentRuntimeVersion"], "manifest": location}
        if not binding["arn"].startswith(f"arn:aws:bedrock-agentcore:{self.settings['region']}:{self.settings['account']}:runtime/gab_journey_"):
            raise ValueError("Runtime target account or resource prefix mismatch")
        return binding

    def ready(self, binding):
        result = self.control.get_agent_runtime(agentRuntimeId=binding["id"], agentRuntimeVersion=binding["version"])
        if result["agentRuntimeArn"] != binding["arn"] or result["agentRuntimeVersion"] != binding["version"]:
            raise ValueError("Runtime version binding mismatch")
        if json.loads(result.get("environmentVariables", {}).get("JOURNEY_MANIFEST", "{}")) != binding["manifest"]:
            raise ValueError("Runtime manifest changed")
        if result["status"] in ("CREATE_FAILED", "UPDATE_FAILED", "DELETING"):
            raise ValueError("AgentCore could not prepare this Runtime")
        if result["status"] != "READY":
            return False
        try:
            endpoint = self.control.get_agent_runtime_endpoint(agentRuntimeId=binding["id"], endpointName="DEFAULT")
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ResourceNotFoundException":
                return False
            raise
        return (endpoint["status"] == "READY" and endpoint["agentRuntimeArn"] == binding["arn"]
                and endpoint["liveVersion"] == binding["version"]
                and endpoint.get("targetVersion", binding["version"]) == binding["version"])

    def invoke(self, binding, definition, text, request_id, history=None, alpr_run_reference=None):
        if not self.ready(binding):
            raise ValueError("Runtime endpoint is not ready for the deployed version")
        response = self.data.invoke_agent_runtime(
            agentRuntimeArn=binding["arn"], qualifier="DEFAULT", runtimeSessionId="gab-" + request_id,
            payload=canonical({"input": text, "request_id": request_id, **({"history": history} if history else {}),
                               **({"alpr_run_reference": alpr_run_reference} if alpr_run_reference else {})}), contentType="application/json",
            accept="application/json")
        stream = response["response"]
        try:
            raw = stream.read(350001)
        finally:
            stream.close()
        if len(raw) > 350000:
            raise ValueError("Runtime returned excessive evidence")
        receipt = json.loads(raw)
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
