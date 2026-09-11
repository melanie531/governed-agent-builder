"""Bounded MCP negotiation, discovery and exact pinned JSON Schema invocation."""
import uuid
from jsonschema import Draft202012Validator

from .admission import recheck
from .config import canonical, digest
from .transport import GatewayError
from opentelemetry import trace

VERSION = '2025-03-26'


class ToolClient:
    def __init__(self, transport, tools):
        self.transport = transport
        self.tools = {t.name: t for t in tools}
        self.ready = False

    def reset(self):
        self.ready = False

    def rpc(self, tool, method, params, budget, telemetry, notification=False):
        budget.require_reservation(self.transport)
        budget.gateway()
        request = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            request['params'] = params
        if not notification:
            request['id'] = str(uuid.uuid4())
        value, metadata = self.transport.post(tool.endpoint, request,
            {'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': VERSION,
             **telemetry.headers()}, budget.remaining())
        telemetry.attributes(trace.get_current_span(), metadata)
        if notification:
            return {}
        if (not isinstance(value, dict) or value.get('jsonrpc') != '2.0'
                or value.get('id') != request['id'] or 'error' in value or not isinstance(value.get('result'), dict)):
            raise GatewayError('MCP_RESPONSE_INVALID')
        return value['result']

    def discover(self, authority, binding, tool, budget, telemetry):
        # Discovery never grants access and every HTTP operation rechecks.
        recheck(authority, binding, 'tool', tool.name)
        result = self.rpc(tool, 'initialize', {'protocolVersion': VERSION,
            'capabilities': {}, 'clientInfo': {'name': 'owned-foundation', 'version': '1'}}, budget, telemetry)
        if result.get('protocolVersion') != VERSION or 'tools' not in result.get('capabilities', {}):
            raise GatewayError('MCP_NEGOTIATION_FAILED')
        recheck(authority, binding, 'tool', tool.name)
        self.rpc(tool, 'notifications/initialized', None, budget, telemetry, notification=True)
        recheck(authority, binding, 'tool', tool.name)
        result = self.rpc(tool, 'tools/list', {}, budget, telemetry)
        if result.get('nextCursor'):
            raise GatewayError('MCP_DISCOVERY_PAGE_CAP')
        entries = result.get('tools', [])
        if not isinstance(entries, list) or len(entries) > 16:
            raise GatewayError('MCP_DISCOVERY_INVALID')
        catalog = {t['name']: t for t in entries}
        if len(catalog) != len(entries):
            raise GatewayError('MCP_DUPLICATE_TOOL')
        for name, selected in self.tools.items():
            found = catalog.get(name)
            if not found or digest(found.get('inputSchema')) != selected.schemaDigest:
                raise GatewayError('PINNED_TOOL_SCHEMA_CHANGED')
        self.ready = True

    def call(self, authority, binding, name, arguments, budget, telemetry):
        tool = self.tools.get(name)
        if tool is None:
            raise GatewayError('UNSELECTED_TOOL')
        if len(canonical(arguments)) > 4096:
            raise GatewayError('TOOL_ARGUMENT_BYTE_CAP')
        if digest(tool.inputSchema) != tool.schemaDigest:
            raise GatewayError('PINNED_TOOL_SCHEMA_CHANGED')
        if not Draft202012Validator(tool.inputSchema).is_valid(arguments):
            raise GatewayError('TOOL_ARGUMENT_SCHEMA_DENIED')
        recheck(authority, binding, 'tool', name)
        budget.require_reservation(self.transport)
        budget.tool()
        with telemetry.span('tool', {'tool': name}) as span:
            if not self.ready:
                self.discover(authority, binding, tool, budget, telemetry)
            recheck(authority, binding, 'tool', name)
            result = self.rpc(tool, 'tools/call', {'name': name, 'arguments': arguments}, budget, telemetry)
            if result.get('isError') is True:
                raise GatewayError('TOOL_RETURNED_ERROR')
            blocks = result.get('content')
            if (not isinstance(blocks, list) or not blocks or len(canonical(blocks)) > 8192
                    or any(b.get('type') != 'text' or not isinstance(b.get('text'), str) for b in blocks)):
                raise GatewayError('UNSUPPORTED_TOOL_CONTENT')
            telemetry.content(span, {'gen_ai.tool.name': name,
                'gen_ai.tool.call.arguments': canonical(arguments).decode(),
                'gen_ai.tool.call.result': '\n'.join(b['text'] for b in blocks)})
            budget.check()
            return [{'type': 'text', 'text': b['text']} for b in blocks]
