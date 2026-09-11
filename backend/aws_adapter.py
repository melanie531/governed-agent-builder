"""Unconnected integration entry point, NOT a deployed AWS implementation.
The application intentionally refuses AWS mode until orchestration/auth/gates are implemented.
This adapter has only offline SDK shape tests. No AWS calls were made during development.
"""
from dataclasses import dataclass
import json
from urllib.parse import urlparse

class IntegrationNotConfigured(RuntimeError):
    pass

@dataclass(frozen=True)
class AWSConfig:
    region: str = ""
    runtime_arn: str = ""
    runtime_qualifier: str = ""
    model_gateway_url: str = ""
    tool_gateway_url: str = ""
    allow_aws_calls: bool = False

    def validate(self):
        missing = [k for k in ("region", "runtime_arn", "runtime_qualifier", "model_gateway_url", "tool_gateway_url") if not getattr(self, k)]
        if missing:
            raise IntegrationNotConfigured("Missing AWS configuration: " + ", ".join(missing) + ". Configure server-side via approved deployment settings; never submit credentials in chat.")
        if not self.allow_aws_calls:
            raise IntegrationNotConfigured("AWS calls require explicit operator approval and allow_aws_calls=True.")
        if not self.runtime_arn.startswith("arn:aws:bedrock-agentcore:"):
            raise IntegrationNotConfigured("Expected an approved AgentCore Runtime ARN.")
        for url in (self.model_gateway_url, self.tool_gateway_url):
            if urlparse(url).scheme != "https":
                raise IntegrationNotConfigured("Gateway endpoints must use HTTPS.")
        if self.model_gateway_url == self.tool_gateway_url:
            raise IntegrationNotConfigured("Configure separate Model Gateway and Tool Gateway instances.")

class AgentCoreRuntimeAdapter:
    def __init__(self, config: AWSConfig, client=None):
        config.validate()
        self.config = config
        if client is None:
            import boto3
            from botocore.config import Config
            client = boto3.client("bedrock-agentcore", region_name=config.region, config=Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 2}))
        self.client = client

    def invoke(self, definition_digest: str, question: str, session_id: str):
        if len(session_id) < 33:
            raise ValueError("AgentCore runtime session ID must have at least 33 characters")
        response = self.client.invoke_agent_runtime(agentRuntimeArn=self.config.runtime_arn, qualifier=self.config.runtime_qualifier, runtimeSessionId=session_id, contentType="application/json", accept="application/json", payload=json.dumps({"definition_digest": definition_digest, "input": question}).encode())
        # Caller must validate the returned version/digest and apply evidence gates.
        return response

@dataclass(frozen=True)
class AgentCoreGatewayAdapter:
    """Distinct configured integration boundary; no unsupported protocol assumptions.
    Authentication, discovery and target-protocol validation are intentionally pending.
    """
    purpose: str
    endpoint: str

    def catalog(self):
        raise IntegrationNotConfigured(f"{self.purpose} Gateway discovery is not integrated. Validate approved target protocols and downstream authentication before enabling AWS mode.")
