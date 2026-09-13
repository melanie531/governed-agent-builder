"""Dispatch-path contract for the reviewed Gateway HTTP passthrough Messages route.

IAMTransport.post guards every non-MCP URL through config.endpoint with the
legacy '/inference/v1/messages' path. The reviewed Opus passthrough binding
uses the dedicated Gateway '/bedrockrt/v1/messages' target path, so the shared
dispatch guard must admit exactly that additional path WITHOUT letting legacy
'messages' manifests carry a passthrough endpoint. No network, credentials or
cloud reads happen here; a stub session with no credentials proves the URL
passes the endpoint gate and still fails closed before signing.
"""
import pytest

from foundation_harness.config import digest, endpoint, load_config
from foundation_harness.transport import GatewayError, IAMTransport
from .test_foundation_executor import config
from .test_opus_modelclient import opus_config

PASSTHROUGH_URL = ('https://gab-foundation-model-m0-example.gateway.bedrock-agentcore'
                   '.us-west-2.amazonaws.com/bedrockrt/v1/messages')
INFERENCE_URL = ('https://gab-foundation-model-m0-example.gateway.bedrock-agentcore'
                 '.us-west-2.amazonaws.com/inference/v1/messages')


class NoCredentialSession:
    def get_credentials(self):
        return None


def test_transport_dispatch_admits_reviewed_passthrough_messages_path():
    # Same call shape as IAMTransport.post for a non-MCP URL.
    assert endpoint(PASSTHROUGH_URL, '/inference/v1/messages') == PASSTHROUGH_URL
    with pytest.raises(GatewayError, match='WORKLOAD_CREDENTIALS_REQUIRED'):
        IAMTransport(NoCredentialSession()).post(
            PASSTHROUGH_URL, {'model': 'us.anthropic.claude-opus-5'},
            {'Accept': 'application/json', 'anthropic-version': '2023-06-01'}, 5)


def test_transport_dispatch_keeps_legacy_inference_path():
    assert endpoint(INFERENCE_URL, '/inference/v1/messages') == INFERENCE_URL


@pytest.mark.parametrize('url', [
    'https://bedrock-runtime.us-west-2.amazonaws.com/anthropic/v1/messages',
    'https://example.com/bedrockrt/v1/messages',
    ('https://gab-foundation-model-m0-example.gateway.bedrock-agentcore'
     '.us-west-2.amazonaws.com/anthropic/v1/messages'),
    ('https://gab-foundation-model-m0-example.gateway.bedrock-agentcore'
     '.us-west-2.amazonaws.com/bedrockrt/v1/messages/extra'),
])
def test_transport_dispatch_rejects_unreviewed_hosts_and_paths(url):
    with pytest.raises(ValueError, match='DEDICATED_GATEWAY_ENDPOINT_REQUIRED'):
        endpoint(url, '/inference/v1/messages')
    with pytest.raises(ValueError, match='DEDICATED_GATEWAY_ENDPOINT_REQUIRED'):
        IAMTransport(NoCredentialSession()).post(url, {}, {}, 5)


def test_legacy_manifest_cannot_carry_passthrough_endpoint():
    # Widening the transport dispatch guard must NOT let a legacy 'messages'
    # manifest bind the passthrough path.
    raw = config()
    raw['model']['endpoint'] = PASSTHROUGH_URL
    with pytest.raises(ValueError):
        load_config(raw, digest(raw))


def test_passthrough_manifest_cannot_carry_legacy_inference_endpoint():
    raw = opus_config()
    raw['model']['endpoint'] = INFERENCE_URL
    with pytest.raises(ValueError):
        load_config(raw, digest(raw))


def test_mcp_dispatch_path_unchanged():
    mcp = ('https://gab-foundation-tools-m0-example.gateway.bedrock-agentcore'
           '.us-west-2.amazonaws.com/mcp')
    assert endpoint(mcp, '/mcp') == mcp
    with pytest.raises(ValueError, match='DEDICATED_GATEWAY_ENDPOINT_REQUIRED'):
        endpoint(PASSTHROUGH_URL, '/mcp')
