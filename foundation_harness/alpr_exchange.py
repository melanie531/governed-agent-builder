"""SigV4 specialist redemption using the existing bounded workload transport.

This separate route is intentionally not registered by current infrastructure.
An opaque capability never goes in a question, trace attribute, or error.
"""
import re

from .config import canonical
from .context import Denied
from .transport import IAMTransport

CALLER_HEADER = "x-gab-alpr-capability"
CHANNEL = "gateway-target-header-runtime-allowlist-v1"
TOOLS = ("agent-alpr-account-vehicle", "agent-alpr-billing-notice", "agent-alpr-remediation")


def is_alpr_tool(name):
    return isinstance(name, str) and name.rsplit("___", 1)[-1] in TOOLS


class SpecialistExchange:
    def __init__(self, session, config):
        required = {"endpoint", "runtime_arn", "runtime_version", "deployment_digest",
                    "channel", "channel_evidence_digest"}
        if (not isinstance(config, dict) or set(config) != required
                or session.region_name != "us-west-2"
                or config["channel"] != CHANNEL
                or not re.fullmatch(r"https://[a-z0-9]+\.execute-api\.us-west-2\.amazonaws\.com/internal/journey/alpr",
                                    config.get("endpoint", ""))
                or not re.fullmatch(r"arn:aws:bedrock-agentcore:us-west-2:\d{12}:runtime/[A-Za-z0-9_-]+",
                                    config.get("runtime_arn", ""))
                or not re.fullmatch(r"[1-9][0-9]*", config.get("runtime_version", ""))
                or any(not re.fullmatch(r"[a-f0-9]{64}", config.get(key, ""))
                       for key in ("deployment_digest", "channel_evidence_digest"))):
            raise Denied("ALPR_VERIFIED_EXCHANGE_CONFIGURATION_REQUIRED")
        self.config, self.transport = dict(config), IAMTransport(session)

    def request(self, reference, operation, **extra):
        from .context import Denied
        if not isinstance(reference, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", reference):
            raise Denied("ALPR_CALLER_REFERENCE_REQUIRED")
        try:
            value, _ = self.transport.send(self.config["endpoint"], canonical({
                "reference": reference, "operation": operation,
                **{key: self.config[key] for key in ("runtime_arn", "runtime_version", "deployment_digest")},
                **extra}), {"Accept": "application/json"}, 3, "execute-api")
        except Exception:
            raise Denied("ALPR_ADMISSION_DENIED") from None
        if not isinstance(value, dict) or value.get("reference") != reference:
            raise Denied("ALPR_CALL_BINDING_DENIED")
        return value

    def list_tools(self, request):
        # A call capability can list only its selected tool, without consuming it.
        # Gateway background synchronization still requires an independently
        # authenticated platform listing adapter. Missing context always denies.
        values = request.headers.getlist(CALLER_HEADER) if request else []
        reference = values[0] if len(values) == 1 else None
        value = self.request(reference, "inspect")
        return [value["tool"]]
