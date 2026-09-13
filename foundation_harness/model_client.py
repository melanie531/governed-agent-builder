"""Claude Messages via an exact dedicated Gateway route."""
from .admission import recheck
from .config import canonical
from .transport import GatewayError


class ModelClient:
    def __init__(self, transport, model):
        self.transport, self.model = transport, model

    def generate(self, authority, binding, system, messages, tools, budget, telemetry):
        recheck(authority, binding, 'model', self.model.route)
        budget.require_reservation(self.transport)
        if self.model.protocol == 'messages-passthrough':
            from .opus_messages import build_request
            if (tools or budget.limits.maxModelCalls != 1 or budget.limits.maxToolCalls != 0
                    or len(messages) != 1 or messages[0].get('role') != 'user'):
                raise GatewayError('OPUS_FIRST_SINGLE_CALL_NO_TOOLS_REQUIRED')
            content = messages[0].get('content')
            if (not isinstance(content, list) or len(content) != 1
                    or not isinstance(content[0], dict) or set(content[0]) != {'type','text'}
                    or content[0]['type'] != 'text'):
                raise GatewayError('OPUS_SINGLE_TEXT_INPUT_REQUIRED')
            body = build_request(self.model.requestModel, system, content[0]['text'],
                                 budget.limits.maxOutputTokens - budget.output_tokens)
        else:
            body = {'model': self.model.route, 'system': system, 'messages': messages,
                    'max_tokens': budget.limits.maxOutputTokens - budget.output_tokens, 'stream': False}
        if tools:
            body['tools'] = [{'name': t.name, 'description': t.description, 'input_schema': t.inputSchema} for t in tools]
        budget.model(body)
        budget.gateway()
        with telemetry.span('model', {'route': self.model.route, 'provider': 'bedrock'}) as span:
            value, metadata = self.transport.post(self.model.endpoint, body,
                {'Accept': 'application/json', 'anthropic-version': '2023-06-01',
                 **telemetry.headers()}, budget.remaining())
            if not isinstance(value, dict) or value.get('type') != 'message':
                raise GatewayError('INVALID_MODEL_RESPONSE')
            budget.usage(value.get('usage'))
            provider_model = value.get('model')
            if self.model.protocol == 'messages-passthrough':
                from .opus_messages import read_response
                read_response(value, self.model.responseModelAllowlist, body['max_tokens'])
            else:
                approved_model = self.model.route.split('/', 1)[1]
                if provider_model not in (approved_model, approved_model.removeprefix('anthropic.')):
                    raise GatewayError('PROVIDER_MODEL_MISMATCH')
            telemetry.attributes(span, {**metadata, 'input_tokens': value['usage']['input_tokens'],
                                        'output_tokens': value['usage']['output_tokens'],
                                        'provider_model': provider_model})
            blocks = value.get('content')
            if (value.get('stop_reason') not in ('end_turn', 'tool_use', 'max_tokens')
                    or not isinstance(blocks, list) or not blocks or len(blocks) > 8
                    or len(canonical(blocks)) > 16384):
                raise GatewayError('INVALID_MODEL_COMPLETION')
            ids = []
            for block in blocks:
                if block.get('type') == 'text':
                    if set(block) != {'type', 'text'} or not isinstance(block['text'], str):
                        raise GatewayError('INVALID_TEXT_BLOCK')
                elif block.get('type') == 'tool_use':
                    if (set(block) != {'type', 'id', 'name', 'input'} or not isinstance(block['input'], dict)
                            or not isinstance(block['id'], str) or not 1 <= len(block['id']) <= 128):
                        raise GatewayError('INVALID_TOOL_USE')
                    ids.append(block['id'])
                else:
                    raise GatewayError('UNSUPPORTED_MODEL_BLOCK')
            if len(set(ids)) != len(ids) or bool(ids) != (value['stop_reason'] == 'tool_use'):
                raise GatewayError('TOOL_CORRELATION_INVALID')
            telemetry.content(span, {'gen_ai.input.messages': canonical(messages).decode(),
                'gen_ai.output.messages': canonical([{'role': 'assistant', 'content': blocks}]).decode(),
                'gen_ai.system_instructions': system})
            budget.check()
            return value
