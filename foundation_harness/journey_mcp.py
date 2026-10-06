"""Native IAM-authenticated AgentCore Gateway MCP transport."""
import json
import re
from uuid import uuid4
from urllib.parse import parse_qs, urlsplit

import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

USER_TOKEN_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Studio-User-Token"


class GatewayFailure(RuntimeError):
    pass


def decode_rpc_response(raw, content_type, request_id):
    if "text/event-stream" not in content_type:
        return json.loads(raw)
    values = []
    for event in raw.decode().replace("\r\n", "\n").split("\n\n"):
        lines = [line[5:].lstrip() for line in event.splitlines() if line.startswith("data:")]
        if lines:
            item = json.loads("\n".join(lines))
            if item.get("id") == request_id:
                values.append(item)
    if len(values) != 1:
        raise GatewayFailure("Server returned an ambiguous MCP response")
    return values[0]


class GatewayAuthorizationRequired(GatewayFailure):
    def __init__(self, challenge):
        super().__init__("Connect your provider account to continue.")
        self.challenge = challenge
        self.completed_tool_calls = None


class GatewayMCP:
    def __init__(self, session, endpoint, *, timeout=25, user_token=None, user_tools=(),
                 auth_type="AWS_IAM", force_authentication=False):
        region = re.escape(session.region_name or "")
        if not re.fullmatch(r"https://[a-zA-Z0-9-]+\.gateway\.bedrock-agentcore\." + region + r"\.amazonaws\.com/mcp", endpoint):
            raise ValueError("An AgentCore Gateway endpoint in the configured region is required")
        self.session, self.endpoint, self.timeout = session, endpoint, timeout
        self.session_id = None
        self.initialized = False
        self._user_token, self.user_tools = user_token, frozenset(user_tools)
        if auth_type not in ("AWS_IAM", "COGNITO"):
            raise ValueError("Unsupported Gateway inbound authentication")
        if type(force_authentication) is not bool or (force_authentication and auth_type != "COGNITO"):
            raise ValueError("Only a Cognito Gateway can force provider authorization")
        self.auth_type = auth_type
        self.force_authentication = force_authentication
        self.protocol_version = "2025-11-25" if auth_type == "COGNITO" else "2025-03-26"

    def rpc(self, method, params, *, notification=False, user_authorized=False):
        body = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            body["id"] = uuid4().hex
        data = json.dumps(body, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": self.protocol_version}
        if user_authorized or self.auth_type == "COGNITO":
            if (not isinstance(self._user_token, str) or len(self._user_token) > 4096
                    or not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", self._user_token)):
                raise GatewayFailure("Sign in to Studio before using this connection.")
            if self.auth_type == "COGNITO":
                headers["Authorization"] = "Bearer " + self._user_token
            else:
                headers[USER_TOKEN_HEADER] = self._user_token
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.auth_type == "AWS_IAM":
            request = AWSRequest(method="POST", url=self.endpoint, data=data, headers=headers)
            SigV4Auth(self.session.get_credentials().get_frozen_credentials(), "bedrock-agentcore",
                      self.session.region_name).add_auth(request)
            headers = dict(request.headers)
        with httpx.Client(timeout=self.timeout, follow_redirects=False) as client:
            with client.stream("POST", self.endpoint, content=data, headers=headers) as response:
                if not 200 <= response.status_code < 300 and not (
                        self.auth_type == "COGNITO" and response.status_code == 400):
                    raise GatewayFailure(f"Gateway returned HTTP {response.status_code}")
                self.session_id = response.headers.get("mcp-session-id", self.session_id)
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 512 * 1024:
                        raise GatewayFailure("Gateway response exceeds the configured size limit")
                if notification:
                    return {}
                value = decode_rpc_response(raw, response.headers.get("content-type", ""), body["id"])
        if (self.auth_type == "COGNITO" and method == "tools/call" and value.get("id") == body["id"]
                and value.get("error", {}).get("code") == -32042):
            elicitations = value["error"].get("data", {}).get("elicitations", [])
            if len(elicitations) == 1:
                url = elicitations[0].get("url", "")
                parsed = urlsplit(url)
                uri = parse_qs(parsed.query).get("request_uri", [])
                if (len(url) <= 8192 and parsed.scheme == "https"
                        and parsed.netloc == f"bedrock-agentcore.{self.session.region_name}.amazonaws.com"
                        and parsed.path == "/identities/oauth2/authorize" and not parsed.fragment
                        and len(uri) == 1 and re.fullmatch(r"urn:ietf:params:oauth:request_uri:[a-zA-Z0-9-._~]+", uri[0])):
                    raise GatewayAuthorizationRequired({"authorization_url": url, "session_uri": uri[0],
                                                        "tool_name": params["name"]})
            raise GatewayFailure("Gateway returned an invalid authorization challenge")
        if value.get("id") != body["id"] or "error" in value or not isinstance(value.get("result"), dict):
            raise GatewayFailure("Gateway MCP request was rejected")
        return value["result"]

    def discover(self):
        if not self.initialized:
            result = self.rpc("initialize", {"protocolVersion": self.protocol_version,
                                             "capabilities": {"elicitation": {"url": {}}} if self.auth_type == "COGNITO" else {},
                                             "clientInfo": {"name": "governed-foundation", "version": "2.0.0"}})
            if "tools" not in result.get("capabilities", {}):
                raise GatewayFailure("Gateway does not advertise tools")
            negotiated = result.get("protocolVersion", self.protocol_version)
            if negotiated not in ("2025-03-26", "2025-11-25"):
                raise GatewayFailure("Gateway negotiated an unsupported MCP protocol version")
            self.protocol_version = negotiated
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
        params = {"name": name, "arguments": arguments}
        if self.force_authentication:
            # Gateway owns its service-linked workload identity. Only the native
            # MCP tool call can invalidate its cached user token and elicit 3LO.
            params["_meta"] = {
                "aws.bedrock-agentcore.gateway/credentialProviderConfiguration": {
                    "oauthCredentialProvider": {"forceAuthentication": True}}}
            self.force_authentication = False
        result = self.rpc("tools/call", params,
                          user_authorized=name in self.user_tools)
        if result.get("isError"):
            raise GatewayFailure("The selected Gateway target could not complete the tool call")
        content = result.get("content", [])
        text = "\n".join(block["text"] for block in content if block.get("type") == "text")
        if not text:
            raise GatewayFailure("The selected Gateway target returned no text")
        return text


class GatewayRoutes:
    """Route selected tools across the retained IAM and Cognito Gateways."""
    def __init__(self, session, manifest, user_token, *, force_authentication=False):
        self.clients, self.tools = {}, {}
        for tool in manifest["tools"]:
            route = (tool.get("gateway_url", manifest["gateway_url"]), tool.get("gateway_auth", "AWS_IAM"))
            self.tools[tool["name"]] = route
            if route not in self.clients:
                self.clients[route] = GatewayMCP(session, route[0], timeout=65, user_token=user_token,
                    auth_type=route[1], force_authentication=force_authentication and route[1] == "COGNITO",
                    user_tools=[t["name"] for t in manifest["tools"]
                                                   if t.get("user_authorization") and t.get("gateway_auth") != "COGNITO"])

    def discover(self):
        tools = [t for route, client in self.clients.items() for t in client.discover()
                 if self.tools.get(t["name"]) == route]
        if len({t["name"] for t in tools}) != len(tools):
            raise GatewayFailure("Gateway tool names are ambiguous")
        return tools

    def call(self, name, arguments):
        if name not in self.tools:
            raise GatewayFailure("Tool is not selected")
        return self.clients[self.tools[name]].call(name, arguments)
