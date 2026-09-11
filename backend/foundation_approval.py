"""Platform-admin reviewed source -> exact immutable definition -> package admission.

No runtime or package hash is an input to compilation. Deployment binds those
later. All writes participate in the repository's serializable CAS transaction.
"""
import copy
import json
import time
from fastapi import HTTPException
from pydantic import Field
from .schemas import Strict
from . import foundation_runs as runs
from foundation_harness.config import digest, load_config
from foundation_harness.package_admission import admission_config


class RegisterFoundation(Strict):
    foundation_id: str
    config: dict
    tool_ids: list[str]
    reason: str = Field(min_length=10, max_length=1000)
    expected_revision: int = Field(ge=0, strict=True)


class ApproveFoundation(Strict):
    agent_id: str
    version: int = Field(ge=1, strict=True)
    definition_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    source_revision: int = Field(ge=1, strict=True)
    expected_revision: int = Field(ge=0, strict=True)
    policy_version: int = Field(ge=1, strict=True)
    epoch: int = Field(ge=0, strict=True)
    request_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{8,100}$')
    reason: str = Field(min_length=10, max_length=1000)


def admin(actor):
    if actor.get('role') != 'admin' or actor.get('workspace') != 'platform':
        raise HTTPException(403, 'VERIFIED_PLATFORM_ADMIN_REQUIRED')


def register(db, actor, data, platform):
    admin(actor)
    from .app import resource
    from scripts.package_foundation import source_digest
    foundation = resource(db, 'foundations', data.foundation_id)
    cfg = load_config(data.config, digest(data.config))
    if (not foundation['approved'] or cfg.foundation.digest != source_digest()
            or len(data.tool_ids) != len(cfg.tools) or len(set(data.tool_ids)) != len(data.tool_ids)):
        raise HTTPException(409, 'REGISTERED_SOURCE_BINDING_REQUIRED')
    bindings = [(cfg.model.id, cfg.model.version, 'model'),
                *[(i, t.version, 'tool') for i, t in zip(data.tool_ids, cfg.tools)],
                *[(s.id, s.version, 'skill') for s in cfg.skills]]
    catalog = {}
    for cid, version, kind in bindings:
        c = resource(db, 'components', cid)
        if not c['approved'] or c['kind'] != kind or c['version'] != version or cid not in foundation[kind+'s']:
            raise HTTPException(409, 'CATALOG_BINDING_REQUIRED')
        catalog[cid] = digest(c)
    key = 'foundation-source:' + data.foundation_id
    old = runs.get(db, key)
    if (old or {}).get('revision', 0) != data.expected_revision:
        raise HTTPException(409, 'SOURCE_REVISION_CONFLICT')
    value = {'revision': data.expected_revision+1, 'config': data.config,
             'tool_ids': data.tool_ids, 'catalog': catalog, 'foundation_digest': digest(foundation),
             'platform': platform, 'registrar': actor['id'], 'registered_at': time.time(), 'reason': data.reason}
    runs.put(db, key, value)
    return value


def compile_approval(db, actor, data, owner, platform):
    admin(actor)
    from .app import get_version, validate_definition, resource, digest as definition_hash, audit
    definition = get_version(db, data.agent_id, data.version)
    if definition['digest'] != data.definition_digest or definition_hash({k:v for k,v in definition.items() if k != 'digest'}) != data.definition_digest:
        raise HTTPException(409, 'IMMUTABLE_DEFINITION_REQUIRED')
    agent = db.select('agents', where=[('id', '=', data.agent_id)]).fetchone()
    if not agent or (agent['owner'], agent['workspace'], agent['current_version']) != (owner['id'], owner['workspace'], data.version):
        raise HTTPException(409, 'CURRENT_OWNER_VERSION_REQUIRED')
    if (actor['id'] == owner['id'] or owner['role'] != 'business'
            or (definition['owner'], definition['workspace']) != (owner['id'], owner['workspace'])):
        raise HTTPException(403, 'INDEPENDENT_OWNER_WORKSPACE_REQUIRED')
    if data.policy_version != runs.get(db, 'policy')['version'] or data.epoch != (runs.get(db, 'foundation-epoch') or 0):
        raise HTTPException(409, 'STALE_REVIEW_GOVERNANCE')
    foundation = validate_definition(db, owner, definition)
    source = runs.get(db, 'foundation-source:' + definition['foundation_id'])
    if not source or source['revision'] != data.source_revision or source['platform'] != platform or source['foundation_digest'] != digest(foundation):
        raise HTTPException(409, 'CURRENT_REGISTERED_SOURCE_REQUIRED')
    selected = [definition['model_id'], *definition['tools'], *definition['skills']]
    if any(source['catalog'].get(i) != digest(resource(db, 'components', i)) for i in selected):
        raise HTTPException(409, 'STALE_CATALOG_BINDING')
    from scripts.package_foundation import source_digest
    if source['config']['foundation']['digest'] != source_digest():
        raise HTTPException(409, 'CURRENT_EXECUTABLE_SOURCE_REQUIRED')
    raw = copy.deepcopy(source['config'])
    if raw['model']['id'] != definition['model_id']:
        raise HTTPException(409, 'REGISTERED_MODEL_REQUIRED')
    tools = dict(zip(source['tool_ids'], raw['tools']))
    skills = {s['id']: s for s in raw['skills']}
    raw.update(name='agent_'+data.agent_id, version=str(data.version), systemPrompt=[{'text': definition['prompt']}],
               tools=[tools[i] for i in definition['tools']], skills=[skills[i] for i in definition['skills']])
    raw['allowedTools'] = [t['name'] for t in raw['tools']]
    for field in ('dataset', 'rubric'):
        raw['evaluation'][field] = {'id': field, 'version': str(data.version), 'digest': digest(definition[field])}
    load_config(raw, digest(raw))
    key = 'foundation-approved:'+data.definition_digest
    old = runs.get(db, key)
    if (old or {}).get('revision', 0) != data.expected_revision or runs.get(db, 'foundation-review:'+data.request_id):
        raise HTTPException(409, 'APPROVAL_REPLAY_OR_REVISION_CONFLICT')
    now = time.time()
    receipt = {'approver': actor['id'], 'approver_role': actor['role'], 'request_id': data.request_id,
               'definition_digest': data.definition_digest, 'version': data.version, 'reviewed_at': now,
               'source_revision': source['revision'], 'reason': data.reason}
    result = {'revision': data.expected_revision+1, 'definition_digest': data.definition_digest,
              'owner': owner['id'], 'workspace': owner['workspace'], 'config': raw,
              'manifest_digest': digest(raw), 'artifact_source_digest': raw['foundation']['digest'],
              'role': platform['role'], 'admission': admission_config(raw, platform['endpoint'], platform['role']),
              'tool_ids': definition['tools'], 'epoch': runs.get(db, 'foundation-epoch') or 0,
              'policy_version': runs.get(db, 'policy')['version'], 'expires_at': now+3600,
              'runtime_admission_reviewed': True, 'receipt': receipt, 'source_revision': source['revision'], 'foundation_id': definition['foundation_id']}
    runs.put(db, key, result)
    runs.put(db, 'foundation-review:'+data.request_id, receipt)
    audit(db, actor['id'], 'foundation_definition_approved', data.agent_id, json.dumps(receipt))
    return result


def platform_metadata(raw=None):
    """Resolve endpoint/role ONLY from current project CFN; verify actual targets."""
    from scripts.foundation_target import StudioTarget, PROJECT, STACK
    import boto3
    target = StudioTarget(boto3.Session(region_name="us-west-2"))
    target.verify()
    cf = target.client('cloudformation')
    def outputs(name):
        return {o['OutputKey']:o['OutputValue'] for o in cf.describe_stacks(StackName=name)['Stacks'][0]['Outputs']}
    app, foundation = outputs(PROJECT+'-serverless-app'), outputs(STACK)
    endpoint = app['ApiEndpoint'].rstrip('/')+'/internal/foundation/exchange'
    role = foundation['FoundationRole']
    if raw is not None:
        control = target.client('bedrock-agentcore-control')
        for output, expected_endpoint in [('ModelGateway', raw['model']['endpoint']), ('ToolsGateway', raw['tools'][0]['endpoint'] if raw['tools'] else None)]:
            gateway = control.get_gateway(gatewayIdentifier=foundation[output])
            base = gateway['gatewayUrl'].removesuffix('/mcp')
            suffix = '/inference/v1/messages' if output == 'ModelGateway' else '/mcp'
            if gateway['status'] != 'READY' or gateway['authorizerType'] != 'AWS_IAM' or (expected_endpoint and expected_endpoint != base+suffix):
                raise HTTPException(409, 'CURRENT_PROJECT_GATEWAY_REQUIRED')
            targets = control.list_gateway_targets(gatewayIdentifier=foundation[output], maxResults=100)
            if targets.get('nextToken') or len(targets['items']) != 1:
                raise HTTPException(409, 'EXACT_PROJECT_TARGET_REQUIRED')
            detail = control.get_gateway_target(gatewayIdentifier=foundation[output], targetId=targets['items'][0]['targetId'])
            if detail['status'] != 'READY':
                raise HTTPException(409, 'READY_TARGET_REQUIRED')
            if output == 'ModelGateway':
                if raw['model']['targetDigest'] != digest(detail['targetConfiguration']):
                    raise HTTPException(409, 'MODEL_TARGET_DIGEST_REQUIRED')
            else:
                inline = detail['targetConfiguration']['mcp']['lambda']['toolSchema']['inlinePayload']
                for tool in raw['tools']:
                    match = next((t for t in inline if detail['name']+'___'+t['name'] == tool['name']), None)
                    if not match or match['inputSchema'] != tool['inputSchema'] or tool['endpoint'] != base+suffix:
                        raise HTTPException(409, 'TOOL_TARGET_SCHEMA_REQUIRED')
    return {'endpoint': endpoint, 'role': role}
