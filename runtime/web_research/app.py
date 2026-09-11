"""AgentCore entrypoint. Manifest is packaged, not accepted from the caller."""
import json
import os
import re
from pathlib import Path
from foundations.web_research import digest
from .gateways import IAMTransport, ModelGateway, ToolGateway, GatewayError
from .harness import run, validate_manifest


def configured_adapters(manifest, config, *, approved=False):
    m = validate_manifest(manifest)
    d = m['definition']
    if (approved is not True or config.get('human_approved') is not True
            or not re.fullmatch(r'\d{12}', config.get('target_account', ''))
            or not re.fullmatch(r'[a-z]{2}-[a-z]+-\d', config.get('region', ''))
            or not 0 < config.get('budget_usd', 0) <= 10
            or not 1 <= config.get('max_output_tokens', 0) <= 4096
            or config.get('max_tool_calls') != len(d['research']['urls'])
            or config.get('max_model_calls') != 1
            or config.get('manifest_digest') != digest(m)):
        raise GatewayError('NOT_CONFIGURED: approved exact manifest, target and budgets required')
    t = m['bindings'][d['tools'][0]]
    model = m['bindings'][d['model_id']]
    if (not config.get('allowed_hosts') or sorted(config['allowed_hosts']) != sorted(t['allowed_hosts'])
            or config.get('site_conditions_approved') is not True):
        raise GatewayError('Approved domain and site conditions required')
    for binding in (t, model):
        if any(not binding.get(key) for key in ('gateway_id', 'target_id', 'target_configuration_digest')):
            raise GatewayError('Approved Gateway target snapshot required')
    region = config['region']
    # Validate all endpoints before asking the role provider for credentials.
    transport = IAMTransport(region)
    tool = ToolGateway(transport, t['endpoint'], t['tool_name'], region)
    model_adapter = ModelGateway(transport, model['endpoint'], model['route'], region, config['max_output_tokens'])
    if t['endpoint'].split('/')[2] == model['endpoint'].split('/')[2]:
        raise GatewayError('Distinct Tool and Model Gateways required')
    # Read-only identity check on explicit LIVE execution only.
    import boto3
    session = boto3.Session(region_name=region)
    if session.client('sts').get_caller_identity()['Account'] != config['target_account']:
        raise GatewayError('Exact target identity mismatch')
    control = session.client('bedrock-agentcore-control')
    for binding in (t, model):
        if not binding.get('gateway_id') or not binding.get('target_id') or not binding.get('target_configuration_digest'):
            raise GatewayError('Approved immutable Gateway target snapshot required')
        gateway = control.get_gateway(gatewayIdentifier=binding['gateway_id'])
        expected_prefix = f"arn:aws:bedrock-agentcore:{region}:{config['target_account']}:gateway/"
        if (gateway.get('status') != 'READY' or gateway.get('authorizerType') != 'AWS_IAM'
                or not gateway.get('gatewayArn', '').startswith(expected_prefix)
                or gateway.get('gatewayUrl', '').split('/')[2] != binding['endpoint'].split('/')[2]):
            raise GatewayError('Gateway target, IAM authorization or readiness mismatch')
        target = control.get_gateway_target(gatewayIdentifier=binding['gateway_id'], targetId=binding['target_id'])
        target_config = target.get('targetConfiguration', {})
        if target.get('status') != 'READY' or digest(target_config) != binding['target_configuration_digest']:
            raise GatewayError('Gateway target configuration changed; reapproval required')
        if binding is model:
            provider = target_config.get('inference', {}).get('provider', {})
            if (provider.get('endpoint') != f'https://bedrock-mantle.{region}.api.aws'
                    or target.get('name') != model['route'].split('/')[0]
                    or not any(op.get('path') == '/v1/messages' for op in provider.get('operations', []))
                    or target.get('credentialProviderConfigurations') != [{'credentialProviderType': 'GATEWAY_IAM_ROLE'}]):
                raise GatewayError('Expected explicit Bedrock IAM inference provider target')
        else:
            if (not target_config.get('mcp', {}).get('lambda')
                    or t['tool_name'] != target.get('name', '') + '___web_fetch'):
                raise GatewayError('Expected pinned Lambda web_fetch target')
    transport.session = session
    return tool, model_adapter


def invoke(payload, context=None):
    if not isinstance(payload, dict) or set(payload) != {'definition_digest'}:
        return {'status': 'DENIED', 'production_ready': False}
    try:
        root = Path(__file__).parent
        manifest = json.loads((root / 'manifest.json').read_text())
        config = json.loads((root / 'execution.json').read_text())
        if (payload['definition_digest'] != manifest['definition']['digest']
                or os.getenv('DEFINITION_DIGEST') != payload['definition_digest']
                or os.getenv('MANIFEST_DIGEST') != digest(manifest)):
            raise GatewayError('Immutable deployment binding mismatch')
        tool, model = configured_adapters(manifest, config, approved=os.getenv('ENABLE_APPROVED_LIVE') == '1')
        return run(manifest, tool, model, mode='live-gateways')
    except Exception:
        return {'status': 'NOT_CONFIGURED_OR_FAILED', 'production_ready': False,
                'message': 'Research execution unavailable; no fixture fallback'}


def create_app():
    from bedrock_agentcore import BedrockAgentCoreApp
    app = BedrockAgentCoreApp()
    app.entrypoint(invoke)
    return app


if __name__ == '__main__':
    create_app().run()
