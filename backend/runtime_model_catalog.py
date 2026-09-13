"""Operator-pinned Runtime routes; target readback is not model enumeration or a grant."""
import copy
import re
from .live_catalog import approved_metadata, revision, validate_exposure, DIGEST_FORMAT


def route_revision(gateway, target, binding):
    return revision({'gatewayArn': gateway['gatewayArn'],
                     'authorizerType': gateway.get('authorizerType'),
                     'policyEngineConfiguration': gateway.get('policyEngineConfiguration'),
                     'roleArn': gateway.get('roleArn'),
                     'interceptorConfigurations': gateway.get('interceptorConfigurations', []),
                     'targetId': target['targetId'], 'name': target['name'],
                     'targetConfiguration': target['targetConfiguration'],
                     'credentialProviderConfigurations': target.get('credentialProviderConfigurations'),
                     'metadataConfiguration': target.get('metadataConfiguration'),
                     'binding': binding})


def validate_source(source, account, region):
    if not isinstance(source, dict) or source.get('approved') is not True or source.get('region') != region:
        raise ValueError('Unapproved Runtime route source')
    gid, tid = source.get('gateway_id'), source.get('target_id')
    if any(not isinstance(v, str) or not re.fullmatch('[A-Za-z0-9-]+', v) for v in (gid, tid)):
        raise ValueError('Exact Runtime Gateway and target required')
    if source.get('gateway_arn') != f'arn:aws:bedrock-agentcore:{region}:{account}:gateway/{gid}':
        raise ValueError('Runtime Gateway account mismatch')
    bindings = source.get('bindings')
    if not isinstance(bindings, list) or not bindings or len(bindings) > 20:
        raise ValueError('Explicit Runtime model bindings required')
    seen = set()
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {'target_name', 'request_model', 'response_models', 'path'}:
            raise ValueError('Exact Runtime binding fields required')
        name, model = binding['target_name'], binding['request_model']
        if (not isinstance(name, str) or not re.fullmatch('[A-Za-z0-9-]+', name)
                or not isinstance(model, str) or not re.fullmatch(r'(?:us|global)\.anthropic\.claude-[A-Za-z0-9._:-]+', model)
                or binding['path'] != '/v1/messages'):
            raise ValueError('Unsupported Runtime Messages binding')
        responses = binding['response_models']
        if (not isinstance(responses, list) or not responses or len(responses) != len(set(responses))
                or any(not isinstance(v, str) or not re.fullmatch(r'anthropic\.claude-[A-Za-z0-9._:-]+', v) for v in responses)):
            raise ValueError('Exact provider response identities required')
        rid = f'model:{gid}:{tid}:{model}'
        if rid in seen:
            raise ValueError('Duplicate Runtime model binding')
        seen.add(rid)
    validate_exposure(source.get('exposure'), f'model:{gid}:{tid}:')
    if set(source['exposure']) != seen:
        raise ValueError('Exposure must exactly match pinned Runtime bindings')


class RuntimeModelCatalog:
    def __init__(self, client, source):
        self.client, self.source = client, copy.deepcopy(source)

    def records(self):
        s = self.source
        gateway = self.client.get_gateway(gatewayIdentifier=s['gateway_id'])
        target = self.client.get_gateway_target(gatewayIdentifier=s['gateway_id'], targetId=s['target_id'])
        if (gateway.get('gatewayArn') != s['gateway_arn'] or gateway.get('status') != 'READY'
                or gateway.get('authorizerType') != 'AWS_IAM' or gateway.get('protocolType') is not None
                or gateway.get('policyEngineConfiguration', {}).get('mode') != 'ENFORCE'
                or not gateway.get('policyEngineConfiguration', {}).get('arn')
                or gateway.get('interceptorConfigurations')
                or target.get('gatewayArn') != s['gateway_arn'] or target.get('targetId') != s['target_id']
                or target.get('status') != 'READY'):
            raise ValueError('Runtime route unavailable or governance changed')
        passthrough = target.get('targetConfiguration', {}).get('http', {}).get('passthrough', {})
        if (passthrough.get('endpoint') != f'https://bedrock-runtime.{s["region"]}.amazonaws.com/anthropic'
                or passthrough.get('protocolType') != 'INFERENCE'
                or target.get('credentialProviderConfigurations') != [{'credentialProviderType': 'GATEWAY_IAM_ROLE',
                    'credentialProvider': {'iamCredentialProvider': {'service': 'bedrock', 'region': s['region']}}}]
                or not {'anthropic-version', 'content-type'} <= set(target.get('metadataConfiguration', {}).get('allowedRequestHeaders', []))):
            raise ValueError('Runtime route/signing contract changed')
        rows = []
        for binding in s['bindings']:
            if target.get('name') != binding['target_name']:
                raise ValueError('Runtime target name mismatch')
            rid = f'model:{s["gateway_id"]}:{s["target_id"]}:{binding["request_model"]}'
            version = route_revision(gateway, target, binding)
            item = approved_metadata(rid, version, 'model', 'AgentCore Model Gateway', s['exposure'], 'Amazon Bedrock')
            if not item or s['exposure'][rid]['approval_sha256'] != version:
                raise ValueError('Runtime route approval stale')
            item.update(name=binding['request_model'], description='Approved Runtime route; execution authorization checked separately',
                        protocol='messages', model_id=binding['request_model'], target_id=s['target_id'],
                        source_version=version, source_revision=version, connector='bedrock-runtime passthrough',
                        discovery_method='operator-pinned-route-with-live-target-readback')
            # Deliberately retain execution_ready=False, integration_ready=False, unverified.
            # A test-role 200 cannot confer workload authorization or a project grant.
            rows.append(item)
        return rows
