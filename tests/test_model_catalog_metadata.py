import pytest
from tools.model_gate.handler import handler


@pytest.mark.parametrize('method,path,body,allowed', [
    ('GET', '/inference/v1/models', '', True),
    ('GET', '/inference/v1/models?x=1', '', False),
    ('GET', '/inference/v1/models/', '', False),
    ('GET', '/inference/v1/messages', '', False),
    ('POST', '/inference/v1/models', '', False),
    ('GET', '/inference/v1/models', 'e30=', False),
    ('GET', '/inference/v1/models', None, False),
])
def test_metadata_route_is_exact(method, path, body, allowed):
    event = {'interceptorInputVersion': '1.0', 'http': {'gatewayRequest': {
        'httpMethod': method, 'path': path, 'body': body}}}
    output = handler(event, None)['http']
    assert ('transformedGatewayRequest' in output) is allowed
    if allowed:
        assert output['transformedGatewayRequest'] == {'body': ''}
    else:
        assert output['transformedGatewayResponse']['statusCode'] == 403
