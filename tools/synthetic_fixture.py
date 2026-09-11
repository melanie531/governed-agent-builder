"""Read-only, network-free M0 transport fixture. This is NOT a Browser adapter."""
SCHEMA = {'type': 'object', 'properties': {'key': {'type': 'string'}}, 'required': ['key']}


def handler(event, context):
    if event != {'key': 'sample'}:
        raise ValueError('SYNTHETIC_KEY_DENIED')
    return {'record': 'sample', 'value': 'A synthetic record for transport verification.'}
