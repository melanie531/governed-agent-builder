"""IAM-only AgentCore Runtime entry. No request can replace the domain manifest."""
import json
import os

from foundation_harness.config import canonical, digest
from foundation_harness.journey_runtime import aws_dependencies, execute


def load_manifest():
    import boto3
    location = json.loads(os.environ["JOURNEY_MANIFEST"])
    s3 = boto3.client("s3")
    response = s3.get_object(Bucket=location["bucket"], Key=location["key"], VersionId=location["version_id"])
    try:
        raw = response["Body"].read(65537)
    finally:
        response["Body"].close()
    if len(raw) > 65536:
        raise ValueError("Manifest size limit exceeded")
    manifest = json.loads(raw)
    if digest(manifest) != location["digest"]:
        raise ValueError("Manifest digest mismatch")
    return manifest


def invoke(payload, context):
    if (not isinstance(payload, dict) or not {"input", "request_id"} <= set(payload)
            or set(payload) - {"input", "request_id", "history"}):
        raise ValueError("Only input, request_id and conversation history may be supplied")
    import boto3
    import re
    import time
    manifest = load_manifest()
    request_id = payload["request_id"]
    if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
        raise ValueError("Invalid request identifier")
    session_id = getattr(context, "session_id", None)
    if not session_id:
        raise ValueError("AgentCore session is required")
    model, gateway = aws_dependencies(manifest)
    s3 = boto3.client("s3", region_name=manifest["region"])
    logs = boto3.client("logs", region_name=manifest["region"])

    def publish(receipt):
        # Actual ended spans, not generated evaluation fixtures.
        log_stream = "agent-" + manifest["agent_id"] + "-v" + str(manifest["version"])
        try:
            logs.create_log_stream(logGroupName=manifest["log_group"], logStreamName=log_stream)
        except logs.exceptions.ResourceAlreadyExistsException:
            pass
        logs.put_log_events(logGroupName=manifest["log_group"], logStreamName=log_stream,
                            logEvents=[{"timestamp": int(time.time() * 1000), "message": canonical(span).decode()}
                                       for span in receipt["spans"]])
        key = manifest["evidence_prefix"] + "/" + manifest["definition_digest"] + "/" + request_id + ".json"
        result = s3.put_object(Bucket=manifest["evidence_bucket"], Key=key, Body=canonical(receipt),
                              ContentType="application/json", ServerSideEncryption="AES256", IfNoneMatch="*")
        return {"key": key, "version_id": result["VersionId"], "digest": digest(receipt)}

    return execute(manifest, payload["input"], session_id, model=model, gateway=gateway, publish=publish,
                   history=payload.get("history"))


def create_app():
    from bedrock_agentcore import BedrockAgentCoreApp
    app = BedrockAgentCoreApp()
    app.entrypoint(invoke)
    return app


if __name__ == "__main__":
    create_app().run()
