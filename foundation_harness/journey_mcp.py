"""Native IAM-authenticated AgentCore Gateway MCP transport."""
import json
import math
import re
from uuid import uuid4

import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from .admission import recheck
from .alpr_exchange import CALLER_HEADER, is_alpr_tool
from .config import digest
from .context import Binding


class GatewayFailure(RuntimeError):
    pass


class GatewayMCP:
    def __init__(self, session, endpoint, *, timeout=25, alpr_admission=None):
        region = re.escape(session.region_name or "")
        if not re.fullmatch(r"https://[a-zA-Z0-9-]+\.gateway\.bedrock-agentcore\." + region + r"\.amazonaws\.com/mcp", endpoint):
            raise ValueError("An AgentCore Gateway endpoint in the configured region is required")
        self.session, self.endpoint, self.timeout = session, endpoint, timeout
        self.session_id = None
        self.initialized = False
        self.alpr_admission, self.alpr_binding = alpr_admission, None

    def admit_alpr(self, manifest, session_id):
        """Host-owned signed run exchange port; no payload/header identity adapter."""
        self.alpr_binding = None
        if self.alpr_admission is None:
            raise GatewayFailure("ALPR_AUTHENTICATED_RUN_EXCHANGE_NOT_CONNECTED")
        binding = self.alpr_admission.resolve()
        if (not isinstance(binding, Binding) or binding.owner != manifest["owner"]
                or binding.workspace != manifest["workspace"] or binding.runtime_session != session_id
                or binding.manifest_digest != digest(manifest)
                or binding.foundation_digest != digest(manifest["foundation"])
                or type(binding.expires_at) not in (int, float) or not math.isfinite(binding.expires_at)):
            raise GatewayFailure("ALPR_IMMUTABLE_RUN_BINDING_DENIED")
        recheck(self.alpr_admission, binding, "start", "")
        self.alpr_binding = binding

    def finish_alpr(self):
        if self.alpr_admission is None or self.alpr_binding is None:
            raise GatewayFailure("ALPR_AUTHENTICATED_RUN_EXCHANGE_NOT_CONNECTED")
        recheck(self.alpr_admission, self.alpr_binding, "finish", "")

    def rpc(self, method, params, *, notification=False, caller_reference=None):
        if caller_reference is not None and (method != "tools/call"
                or not is_alpr_tool(params.get("name"))
                or not isinstance(caller_reference, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{43}", caller_reference)
                or self.alpr_binding is None):
            raise GatewayFailure("ALPR_CALLER_REFERENCE_REQUIRED")
        if method == "tools/call" and is_alpr_tool(params.get("name")) and caller_reference is None:
            raise GatewayFailure("ALPR_CALLER_REFERENCE_REQUIRED")
        body = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            body["id"] = uuid4().hex
        data = json.dumps(body, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": "2025-03-26"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if caller_reference:
            # Supported only with target metadataConfiguration.allowedRequestHeaders
            # AND Runtime requestHeaderAllowlist, independently host-smoked.
            headers[CALLER_HEADER] = caller_reference
        request = AWSRequest(method="POST", url=self.endpoint, data=data, headers=headers)
        SigV4Auth(self.session.get_credentials().get_frozen_credentials(), "bedrock-agentcore",
                  self.session.region_name).add_auth(request)
        with httpx.Client(timeout=self.timeout, follow_redirects=False) as client:
            with client.stream("POST", self.endpoint, content=data, headers=dict(request.headers)) as response:
                if not 200 <= response.status_code < 300:
                    raise GatewayFailure(f"Gateway returned HTTP {response.status_code}")
                self.session_id = response.headers.get("mcp-session-id", self.session_id)
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 512 * 1024:
                        raise GatewayFailure("Gateway response exceeds the configured size limit")
                if notification:
                    return {}
                if "text/event-stream" in response.headers.get("content-type", ""):
                    values = []
                    for event in raw.decode().replace("\r\n", "\n").split("\n\n"):
                        lines = [line[5:].lstrip() for line in event.splitlines() if line.startswith("data:")]
                        if lines:
                            item = json.loads("\n".join(lines))
                            if item.get("id") == body["id"]:
                                values.append(item)
                    if len(values) != 1:
                        raise GatewayFailure("Gateway returned an ambiguous MCP response")
                    value = values[0]
                else:
                    value = json.loads(raw)
        if value.get("id") != body["id"] or "error" in value or not isinstance(value.get("result"), dict):
            raise GatewayFailure("Gateway MCP request was rejected")
        return value["result"]

    def discover(self):
        if not self.initialized:
            result = self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                             "clientInfo": {"name": "governed-foundation", "version": "2.0.0"}})
            if "tools" not in result.get("capabilities", {}):
                raise GatewayFailure("Gateway does not advertise tools")
            self.rpc("notifications/initialized", {}, notification=True)
            self.initialized = True
        tools, cursor = [], None
        for _ in range(10):
            page = self.rpc("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(page.get("tools", []))
            cursor = page.get("nextCursor")
            if not cursor:
                if len({tool["name"] for tool in tools}) != len(tools):
                    raise GatewayFailure("Gateway returned duplicate tool names")
                return tools
        raise GatewayFailure("Gateway tool catalog exceeds the discovery limit")

    def call(self, name, arguments):
        kwargs = {}
        if is_alpr_tool(name):
            if self.alpr_admission is None or self.alpr_binding is None:
                raise GatewayFailure("ALPR_AUTHENTICATED_RUN_EXCHANGE_NOT_CONNECTED")
            recheck(self.alpr_admission, self.alpr_binding, "tool", name)
            kwargs["caller_reference"] = self.alpr_admission.issue(self.alpr_binding, name, arguments)
        result = self.rpc("tools/call", {"name": name, "arguments": arguments}, **kwargs)
        if result.get("isError"):
            raise GatewayFailure("The selected Gateway target could not complete the tool call")
        content = result.get("content", [])
        text = "\n".join(block["text"] for block in content if block.get("type") == "text")
        if not text:
            raise GatewayFailure("The selected Gateway target returned no text")
        return text
