"""Renewable SigV4 over exact HTTP bytes. No retry, redirects or provider fallback."""
import asyncio
import json
import math
from contextvars import ContextVar
from contextlib import contextmanager

_CAPTURE_DEADLINE = ContextVar('capture_deadline', default=None)


@contextmanager
def capture_deadline(deadline, clock):
    token = _CAPTURE_DEADLINE.set((deadline, clock))
    try:
        yield
    finally:
        _CAPTURE_DEADLINE.reset(token)


def remaining_timeout(timeout):
    boundary = _CAPTURE_DEADLINE.get()
    if boundary is not None:
        deadline, clock = boundary
        now = clock()
        if type(now) not in (int, float) or not math.isfinite(now):
            raise TimeoutError('CAPTURE_CLOCK_INVALID')
        timeout = min(timeout, deadline - now)
    if not math.isfinite(timeout) or timeout <= 0:
        raise TimeoutError('CAPTURE_DEADLINE_EXCEEDED')
    return timeout

import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from .config import canonical, endpoint


class GatewayError(RuntimeError):
    pass


class _FinalDeadlineTransport(httpx.AsyncBaseTransport):
    """Enforce the capture deadline at the ABSOLUTE final send boundary.

    The before_send event hook runs during request preparation; a deadline can
    still be crossed AFTER that hook but BEFORE the actual dispatch (pool acquire,
    DNS/TLS, etc.). This transport wraps the real one and re-checks the deadline as
    the very first action of handle_async_request -- the last gate before bytes go
    out -- so a deadline crossed during construction yields ZERO sends. No retry;
    it only reads the ContextVar deadline and never mutates other state.
    """

    def __init__(self, inner):
        self._inner = inner

    async def handle_async_request(self, request):
        # Final gate: if the deadline has passed by now, do NOT dispatch.
        remaining_timeout(_finite_positive_from(request))
        return await self._inner.handle_async_request(request)

    async def aclose(self):
        await self._inner.aclose()


def _finite_positive_from(request):
    # Use the request's already-bounded timeout as the input ceiling; the deadline
    # in the ContextVar is what actually gates. Fallback to a large finite ceiling.
    extensions = getattr(request, 'extensions', {}) or {}
    timeout = extensions.get('timeout')
    if isinstance(timeout, dict):
        values = [v for v in timeout.values() if isinstance(v, (int, float))]
        if values:
            return max(values)
    return 60


class IAMTransport:
    requires_reservation = True

    def __init__(self, session):
        self.session = session

    def post(self, url, body, headers, timeout):
        endpoint(url, '/mcp' if url.endswith('/mcp') else '/inference/v1/messages')
        return self.send(url, canonical(body), headers, timeout, 'bedrock-agentcore')

    def send(self, url, data, headers, timeout, service):
        # Caller may only set protocol/telemetry headers; never identity/WAT.
        allowed = {'Content-Type', 'Accept', 'anthropic-version', 'MCP-Protocol-Version', 'traceparent'}
        if service == 'xray':
            allowed |= {'x-aws-log-group', 'x-aws-log-stream'}
        if set(headers) - allowed:
            raise GatewayError('UNSUPPORTED_REQUEST_HEADER')
        credentials = self.session.get_credentials()
        if credentials is None:
            raise GatewayError('WORKLOAD_CREDENTIALS_REQUIRED')
        request = AWSRequest(method='POST', url=url, data=data,
                             headers={'Content-Type': 'application/json', **headers})
        SigV4Auth(credentials.get_frozen_credentials(), service, 'us-west-2').add_auth(request)
        # Credentials/signing may block; recheck the absolute capture deadline
        # after both and again inside the network coroutine.
        timeout = remaining_timeout(timeout)
        return asyncio.run(self._send(url, data, dict(request.headers), timeout))

    async def _send(self, url, data, headers, timeout):
        # asyncio timeout bounds the whole response, including slow chunk streams.
        timeout = remaining_timeout(timeout)
        async with asyncio.timeout(min(timeout, 60)):
            async def before_send(request):
                # HTTPX request hooks run after build/auth preparation, immediately
                # before transport dispatch. Recompute, never reuse pre-build time.
                remaining = remaining_timeout(timeout)
                request.extensions['timeout'] = httpx.Timeout(min(remaining, 10)).as_dict()

            async with httpx.AsyncClient(follow_redirects=False, trust_env=False,
                                         event_hooks={'request': [before_send]},
                                         timeout=httpx.Timeout(min(timeout, 10))) as client:
                # Wrap whatever transport is in effect (real or injected) so the
                # capture deadline is re-checked at the ABSOLUTE final gate --
                # inside handle_async_request, after all request construction and
                # event hooks, immediately before bytes are dispatched.
                client._transport = _FinalDeadlineTransport(client._transport)
                remaining = remaining_timeout(timeout)
                async with client.stream('POST', url, content=data, headers=headers,
                                         timeout=httpx.Timeout(min(remaining, 10))) as response:
                    if not 200 <= response.status_code < 300:
                        # Payload may contain prompts/credentials. Do not log it.
                        raise GatewayError(f'GATEWAY_HTTP_{response.status_code}')
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 65536:
                            raise GatewayError('RESPONSE_BYTE_CAP')
                    kind = response.headers.get('content-type', '').split(';')[0]
                    if not raw and (response.status_code in (202, 204) or url.endswith('/v1/traces')):
                        value = None
                    elif kind == 'application/x-protobuf' and url.endswith('/v1/traces'):
                        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse
                        decoded = ExportTraceServiceResponse()
                        decoded.ParseFromString(raw)
                        if decoded.partial_success.rejected_spans or decoded.partial_success.error_message:
                            raise GatewayError('OTLP_PARTIAL_REJECTION')
                        value = None
                    elif kind == 'application/json':
                        value = json.loads(raw)
                    elif kind == 'text/event-stream' and url.endswith('/mcp'):
                        # MCP permits SSE; this never parses model streaming.
                        events = []
                        for event in raw.decode().replace('\r\n', '\n').split('\n\n'):
                            lines = [s[5:].lstrip() for s in event.splitlines() if s.startswith('data:')]
                            if lines:
                                events.append(json.loads('\n'.join(lines)))
                        values = [v for v in events if 'result' in v or 'error' in v]
                        if len(values) != 1:
                            raise GatewayError('MCP_RESPONSE_AMBIGUOUS')
                        value = values[0]
                    else:
                        raise GatewayError('UNSUPPORTED_RESPONSE_CONTENT_TYPE')
                    request_id = response.headers.get('x-amzn-requestid', response.headers.get('request-id'))
                    return value, {'request_id': request_id} if request_id else {}
