"""Pure bounded Opus Messages codec; no network, credentials, grants or readiness.

This codec does not replace ModelClient authority, reservation or signed transport.
Its caller must enforce one call/no retries and bind the response allowlist into
an approved manifest. No production response identities are inferred here.
"""
from .config import canonical

REQUEST_MODEL = 'us.anthropic.claude-opus-5'


def _limit(value):
    if type(value) is not int or not 1 <= value <= 256:
        raise ValueError('OPUS_OUTPUT_BUDGET_INVALID')


def build_request(request_model, system, prompt, max_tokens):
    if request_model != REQUEST_MODEL:
        raise ValueError('OPUS_EXACT_PROFILE_REQUIRED')
    _limit(max_tokens)
    if not isinstance(system, str) or not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('OPUS_TEXT_INPUT_REQUIRED')
    body = {'model': request_model, 'system': system,
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': max_tokens, 'stream': False, 'thinking': {'type': 'disabled'}}
    # Byte bound is not advertised as a token bound; runtime token budget remains mandatory.
    if len(canonical(body)) > 16384:
        raise ValueError('OPUS_REQUEST_TOO_LARGE')
    return body


def read_response(value, allowed_response_models, max_tokens):
    _limit(max_tokens)
    if (not isinstance(allowed_response_models, tuple) or not allowed_response_models
            or any(not isinstance(m, str) or not m for m in allowed_response_models)):
        raise ValueError('EXPLICIT_RESPONSE_ID_ALLOWLIST_REQUIRED')
    if (not isinstance(value, dict) or value.get('type') != 'message'
            or value.get('model') not in allowed_response_models):
        raise ValueError('OPUS_RESPONSE_IDENTITY_MISMATCH')
    usage = value.get('usage')
    if (not isinstance(usage, dict)
            or any(type(usage.get(k)) is not int or usage[k] < 0
                   for k in ('input_tokens', 'output_tokens'))
            or usage['output_tokens'] > max_tokens):
        raise ValueError('OPUS_USAGE_INVALID')
    if value.get('stop_reason') not in ('end_turn', 'max_tokens'):
        raise ValueError('OPUS_COMPLETION_INVALID')
    blocks = value.get('content')
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= 8:
        raise ValueError('OPUS_TEXT_RESPONSE_REQUIRED')
    for block in blocks:
        if (not isinstance(block, dict) or set(block) != {'type', 'text'}
                or block['type'] != 'text' or not isinstance(block['text'], str)):
            raise ValueError('OPUS_UNEXPECTED_RESPONSE_BLOCK')
    text = ''.join(b['text'] for b in blocks)
    if not text.strip() or len(text.encode()) > 16384:
        raise ValueError('OPUS_TEXT_RESPONSE_INVALID')
    return text
