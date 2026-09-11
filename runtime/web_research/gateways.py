"""Documented AgentCore HTTPS data plane, with server-side IAM only.

No client creation or credential lookup at import/startup. No direct Bedrock fallback.
"""
import json
import re
import time
import uuid
from urllib.parse import urlsplit


class GatewayError(RuntimeError):
    pass


def gateway_endpoint(url, region, path):
    u = urlsplit(url)
    if (u.scheme != 'https' or u.username or u.password or u.port is not None
            or u.query or u.fragment or u.path != path
            or not re.fullmatch(r'[a-z0-9-]+\.gateway\.bedrock-agentcore\.' + re.escape(region) + r'\.amazonaws\.com', u.netloc)):
        raise GatewayError('An exact AgentCore Gateway endpoint is required')
    return url


class IAMTransport:
    def __init__(self, region, *, session=None):
        self.region = region
        self.session = session

    def post(self, url, body, headers=None):
        # Only instantiated by an explicit approved live configuration.
        import boto3
        import httpx
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest
        try:
            session = self.session or boto3.Session(region_name=self.region)
            credentials = session.get_credentials()
            if credentials is None:
                raise GatewayError('Runtime IAM role required')
            data = json.dumps(body, separators=(',', ':')).encode()
            request = AWSRequest(method='POST', url=url, data=data, headers={
                'Content-Type': 'application/json', **(headers or {})})
            SigV4Auth(credentials.get_frozen_credentials(), 'bedrock-agentcore', self.region).add_auth(request)
            started = time.monotonic()
            with httpx.Client(timeout=httpx.Timeout(30, connect=5), follow_redirects=False, trust_env=False) as client:
                with client.stream('POST', url, content=data, headers=dict(request.headers)) as response:
                    if response.status_code != 200:
                        raise GatewayError('Gateway rejected request')
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 1048576 or time.monotonic() - started > 45:
                            raise GatewayError('Gateway response limit exceeded')
                    kind = response.headers.get('content-type', '').split(';')[0]
                    if kind == 'text/event-stream':
                        events = [json.loads(line[5:].strip()) for line in raw.decode().splitlines()
                                  if line.startswith('data:') and line[5:].strip() != '[DONE]']
                        events = [e for e in events if 'result' in e or 'error' in e]
                        if len(events) != 1:
                            raise GatewayError('Unsupported MCP event response')
                        result = events[0]
                    elif kind == 'application/json':
                        result = json.loads(raw)
                    else:
                        raise GatewayError('Unsupported Gateway response')
                    # Return only actual response IDs. No invented AWS trace ARN.
                    telemetry = {k: response.headers[k] for k in ('x-amzn-requestid', 'x-amzn-trace-id', 'request-id') if k in response.headers}
                    return result, telemetry
        except Exception:
            raise GatewayError('Gateway request failed; no fixture fallback') from None


class ToolGateway:
    def __init__(self, transport, endpoint, tool_name, region):
        self.endpoint = gateway_endpoint(endpoint, region, '/mcp')
        if not re.fullmatch(r'[A-Za-z0-9_-]+___web_fetch', tool_name):
            raise GatewayError('Pinned target-qualified web_fetch tool required')
        self.transport, self.tool_name = transport, tool_name

    def fetch(self, url):
        request_id = str(uuid.uuid4())
        response, telemetry = self.transport.post(self.endpoint, {
            'jsonrpc': '2.0', 'id': request_id, 'method': 'tools/call',
            'params': {'name': self.tool_name, 'arguments': {'url': url}}},
            {'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-11-25'})
        if response.get('id') != request_id or 'error' in response or response.get('result', {}).get('isError'):
            raise GatewayError('Tool Gateway call failed')
        result = response.get('result', {})
        value = result.get('structuredContent')
        if value is None:
            blocks = result.get('content', [])
            if len(blocks) != 1 or blocks[0].get('type') != 'text':
                raise GatewayError('Expected one JSON tool result')
            try:
                value = json.loads(blocks[0]['text'])
            except Exception:
                raise GatewayError('Invalid tool evidence') from None
        if not isinstance(value, dict) or 'error' in value:
            raise GatewayError('Fetch denied by tool')
        return value, telemetry


class ModelGateway:
    def __init__(self, transport, endpoint, route, region, max_tokens):
        self.endpoint = gateway_endpoint(endpoint, region, '/inference/v1/messages')
        # Qualified routing prevents ambiguous target selection. First slice is Claude only.
        if not re.fullmatch(r'[a-zA-Z0-9_-]+/(?:global\.|us\.|eu\.|apac\.)?anthropic\.claude-[a-zA-Z0-9:._-]+', route):
            raise GatewayError('Approved target-qualified Claude/Bedrock route required')
        if not 1 <= max_tokens <= 4096:
            raise GatewayError('Bounded output token budget required')
        self.transport, self.route, self.max_tokens = transport, route, max_tokens

    def report(self, system, user):
        value, telemetry = self.transport.post(self.endpoint, {
            'model': self.route, 'max_tokens': self.max_tokens, 'stream': False,
            'system': system, 'messages': [{'role': 'user', 'content': user}]},
            {'Accept': 'application/json', 'anthropic-version': '2023-06-01'})
        blocks = value.get('content', [])
        if (value.get('type') != 'message' or value.get('stop_reason') != 'end_turn'
                or not blocks or any(b.get('type') != 'text' for b in blocks)):
            raise GatewayError('Incomplete report or unauthorized tool request')
        text = ''.join(b['text'] for b in blocks)
        if not text or len(text) > 65536:
            raise GatewayError('Invalid model report size')
        return text, telemetry
