"""Offline SDK wire validation only, not an emitter or deployment adapter.

Create requires name/role; update uses optionalValue wrappers; invoke has a
session/messages and named endpoint qualifier, not role/memory/environment.
No caller of this helper gains execution authority. No SDK Session/client.
"""
import re
from botocore.loaders import Loader
from botocore.model import ServiceModel
from botocore.validate import ParamValidator

OPERATIONS = {'create': ('bedrock-agentcore-control', 'CreateHarness'),
              'update': ('bedrock-agentcore-control', 'UpdateHarness'),
              'invoke': ('bedrock-agentcore', 'InvokeHarness')}


def validate_wire_shape(operation, payload):
    if operation not in OPERATIONS:
        raise ValueError('UNKNOWN_OPERATION')
    # Omission would default to direct Bedrock, or retain stale update settings.
    if not payload.get('model') or 'allowedTools' not in payload:
        raise ValueError('EXPLICIT_MODEL_AND_ALLOWLIST_REQUIRED')
    if not isinstance(payload['model'], dict) or len(payload['model']) != 1:
        raise ValueError('EXACTLY_ONE_MODEL_PROTOCOL_REQUIRED')
    if any(not re.fullmatch(r'@[A-Za-z][A-Za-z0-9_]*/[A-Za-z][A-Za-z0-9_-]*', t)
           or t.rsplit('/', 1)[-1].lower() in {'shell', 'file_operations', 'invokeagentruntimecommand'}
           for t in payload['allowedTools']):
        raise ValueError('FORBIDDEN_TOOL_ALLOWLIST')
    service, name = OPERATIONS[operation]
    shape = ServiceModel(Loader().load_service_model(service, 'service-2')).operation_model(name).input_shape
    if ParamValidator().validate(payload, shape).has_errors():
        raise ValueError('INVALID_NATIVE_WIRE_SHAPE')
    if operation == 'create' and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', payload['harnessName']):
        raise ValueError('INVALID_NATIVE_NAME')
    if operation == 'invoke':
        if not 33 <= len(payload['runtimeSessionId']) <= 100:
            raise ValueError('INVALID_NATIVE_SESSION')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', payload.get('qualifier', '')):
            raise ValueError('PINNED_ENDPOINT_REQUIRED')
    return {'wire_shape_valid': True, 'execution_ready': False,
            'readiness': ['BLOCKED_NATIVE_INTEGRATION'], 'operation': name}
