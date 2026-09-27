"""SigV4 ALPR admission using the existing bounded workload transport.

The dedicated route is installed by the host's additive release command.
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
        values = request.headers.getlist(CALLER_HEADER) if request else []
        if request is not None and not values:
            # Runtime ingress is IAM-only and explicitly Gateway-only. The
            # backend verifies that policy and B's exclusive workload role.
            # This operation returns schemas only, never call authority.
            try:
                value, _ = self.transport.send(self.config["endpoint"], canonical({
                    "operation": "platform-list",
                    **{key: self.config[key] for key in ("runtime_arn", "runtime_version", "deployment_digest")}
                }), {"Accept": "application/json"}, 3, "execute-api")
            except Exception:
                raise Denied("ALPR_PLATFORM_LISTING_DENIED") from None
            if (not isinstance(value, dict) or value.get("deployment_digest") != self.config["deployment_digest"]
                    or value.get("tools") != list(TOOLS)):
                raise Denied("ALPR_PLATFORM_LISTING_DENIED")
            return value["tools"]
        reference = values[0] if len(values) == 1 else None
        value = self.request(reference, "inspect")
        return [value["tool"]]


class RunExchange:
    """Runtime A uses its workload SigV4 session plus a backend-issued run reference."""
    def __init__(self, session, endpoint, reference, invocation):
        from .config import digest
        if (session.region_name != 'us-west-2' or not isinstance(endpoint, str)
                or not re.fullmatch(r'https://[a-z0-9]+\.execute-api\.us-west-2\.amazonaws\.com/internal/journey/alpr', endpoint)
                or not isinstance(reference, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', reference)):
            raise Denied('ALPR_RUN_EXCHANGE_CONFIGURATION_REQUIRED')
        self.transport, self.endpoint, self.reference = IAMTransport(session), endpoint, reference
        self.invocation_digest, self.binding = digest(invocation), None

    def request(self, operation, **extra):
        try:
            value, _ = self.transport.send(self.endpoint, canonical({
                'operation': operation, 'run_reference': self.reference, **extra}),
                {'Accept': 'application/json'}, 15, 'execute-api')
        except Exception:
            raise Denied('ALPR_RUN_ADMISSION_DENIED') from None
        from .config import digest
        from .context import Binding
        if (not isinstance(value, dict) or not isinstance(value.get('binding'), dict)
                or digest(value['binding']) != value.get('binding_digest')):
            raise Denied('ALPR_IMMUTABLE_RUN_BINDING_DENIED')
        bound = Binding(**value['binding'])
        if self.binding is not None and bound != self.binding:
            raise Denied('ALPR_IMMUTABLE_RUN_BINDING_DENIED')
        return value, bound

    def resolve(self):
        _, self.binding = self.request('resolve-run', invocation_digest=self.invocation_digest)
        return self.binding

    def authorize(self, binding, operation, resource):
        from dataclasses import asdict
        from .config import digest
        if binding != self.binding:
            raise Denied('ALPR_IMMUTABLE_RUN_BINDING_DENIED')
        self.request('authorize-run', binding_digest=digest(asdict(binding)), action=operation, resource=resource)

    def issue(self, binding, qualified_tool, arguments):
        from dataclasses import asdict
        from .config import digest
        if binding != self.binding:
            raise Denied('ALPR_IMMUTABLE_RUN_BINDING_DENIED')
        value, _ = self.request('issue-call', binding_digest=digest(asdict(binding)),
                                tool=qualified_tool, arguments=arguments)
        reference = value.get('reference')
        if not isinstance(reference, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', reference):
            raise Denied('ALPR_CALLER_REFERENCE_REQUIRED')
        return reference
