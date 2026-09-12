"""Narrow HTTP REQUEST interceptor; IAM admission is NOT current-user authority."""
import base64
import binascii
import json
import logging
import re

APPROVED_MODEL = 'claude/anthropic.claude-haiku-4-5'
PATH = '/inference/v1/messages'
MAX_BODY_BYTES = 1744  # plus the foundation budget's 256-byte allowance <= 2000
MAX_OUTPUT = 256
LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)


class Rejected(ValueError):
    pass


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise Rejected('DUPLICATE_KEY')
        result[key] = value
    return result


def bad_constant(_):
    raise Rejected('NONFINITE_JSON')


def validate(event):
    if not isinstance(event, dict) or event.get('interceptorInputVersion') != '1.0' or 'mcp' in event:
        raise Rejected('EVENT_INVALID')
    http = event.get('http')
    if not isinstance(http, dict) or set(http) != {'gatewayRequest'}:
        raise Rejected('EVENT_INVALID')
    request = http['gatewayRequest']
    if not isinstance(request, dict) or set(request) - {'path', 'httpMethod', 'body', 'headers'}:
        raise Rejected('EVENT_INVALID')
    # Documented metadata enumeration only. Never authorize arbitrary GET paths,
    # query variants or a request body; all inference validation stays below.
    if request.get('path') == '/inference/v1/models' and request.get('httpMethod') == 'GET':
        if request.get('body', '') != '':
            raise Rejected('METADATA_BODY_DENIED')
        return ''
    if request.get('path') != PATH or request.get('httpMethod') != 'POST':
        raise Rejected('OPERATION_DENIED')
    # Headers are never identity and never forwarded by this function.
    body = request.get('body')
    if not isinstance(body, str) or len(body) > 4 * ((MAX_BODY_BYTES + 2) // 3):
        raise Rejected('BODY_LIMIT')
    raw = base64.b64decode(body, validate=True)
    if len(raw) > MAX_BODY_BYTES or base64.b64encode(raw).decode('ascii') != body:
        raise Rejected('BODY_LIMIT')
    payload = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=bad_constant)
    if not isinstance(payload, dict):
        raise Rejected('JSON_INVALID')
    if set(payload) - {'model', 'messages', 'system', 'max_tokens', 'stream'}:
        raise Rejected('CONTROL_FIELD_DENIED')
    if payload.get('model') != APPROVED_MODEL:
        raise Rejected('MODEL_DENIED')
    if 'stream' in payload and payload['stream'] is not False:
        raise Rejected('STREAM_DENIED')
    if type(payload.get('max_tokens')) is not int or not 1 <= payload['max_tokens'] <= MAX_OUTPUT:
        raise Rejected('OUTPUT_LIMIT')
    if 'system' in payload and not isinstance(payload['system'], str):
        raise Rejected('SYSTEM_INVALID')
    messages = payload.get('messages')
    if not isinstance(messages, list) or not 1 <= len(messages) <= 8:
        raise Rejected('MESSAGES_INVALID')
    for message in messages:
        if (not isinstance(message, dict) or set(message) != {'role', 'content'}
                or message['role'] not in ('user', 'assistant')
                or not isinstance(message['content'], str) or not message['content']):
            raise Rejected('MESSAGES_INVALID')
    if messages[0]['role'] != 'user':
        raise Rejected('MESSAGES_INVALID')
    # Canonical reconstruction prevents duplicate/control fields reaching provider.
    payload['stream'] = False
    encoded = json.dumps(payload, separators=(',', ':'), ensure_ascii=True).encode('utf-8')
    if len(encoded) > MAX_BODY_BYTES:
        raise Rejected('BODY_LIMIT')
    return base64.b64encode(encoded).decode('ascii')


def handler(event, context):
    code = 'ALLOW'
    try:
        body = validate(event)
        output = {'interceptorOutputVersion': '1.0', 'http': {
            'transformedGatewayRequest': {'body': body}}}
    except (Rejected, ValueError, TypeError, KeyError, UnicodeError, binascii.Error, RecursionError):
        code = 'MODEL_GATE_DENIED'
        output = {'interceptorOutputVersion': '1.0', 'http': {'transformedGatewayResponse': {
            'statusCode': 403, 'contentType': 'application/json',
            'body': base64.b64encode(b'{"error":{"code":"MODEL_GATE_DENIED"}}').decode('ascii')}}}
    # No event/body/header/account/IP/exception text. Client context is not identity.
    custom = getattr(getattr(context, 'client_context', None), 'custom', {}) or {}
    rid = custom.get('REQUEST_ID') if isinstance(custom, dict) else None
    rid = rid if isinstance(rid, str) and re.fullmatch(r'[a-fA-F0-9-]{16,64}', rid) else None
    LOG.info(json.dumps({'decision': code, 'gateway_request_id': rid}))
    return output
