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
    if (runtime_model_config(cfg.model)
            and platform.get('model') != cfg.model.model_dump(mode='json')):
        raise HTTPException(409, 'REGISTERED_RUNTIME_MODEL_BINDING_REQUIRED')
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
    registered_model({'config': data.config, 'platform': platform}, cfg.model.id)
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
    registered_model(source, definition['model_id'])
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
    if runs.get(db, 'foundation-artifact:' + data.definition_digest):
        raise HTTPException(409, 'FINALIZED_APPROVAL_IMMUTABLE')
    old = runs.get(db, key)
    if (old or {}).get('revision', 0) != data.expected_revision or runs.get(db, 'foundation-review:'+data.request_id):
        raise HTTPException(409, 'APPROVAL_REPLAY_OR_REVISION_CONFLICT')
    now = time.time()
    receipt = {'provenance': 'M0', 'approver': actor['id'], 'approver_role': actor['role'], 'request_id': data.request_id,
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
        cfg = load_config(raw, digest(raw))
        control = target.client('bedrock-agentcore-control')
        runtime_model = None
        gateways = [('ModelGateway', raw['model']['endpoint']),
                    ('ToolsGateway', raw['tools'][0]['endpoint'] if raw['tools'] else None)]
        if runtime_model_config(cfg.model):
            runtime_model = runtime_platform_model(control, target.account, cfg.model)
            # No tool gateway/list permission is required for a no-tools source.
            gateways = [('ToolsGateway', raw['tools'][0]['endpoint'])] if raw['tools'] else []
        for output, expected_endpoint in gateways:
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
    result = {'endpoint': endpoint, 'role': role}
    if raw is not None and runtime_model is not None:
        result['model'] = runtime_model
    return result


def runtime_platform_model(control, account, model):
    from .live_catalog import catalog_config
    from .runtime_model_catalog import validate_source, foundation_model
    config = catalog_config() or {}
    if not isinstance(config, dict) or not isinstance(config.get('binding'), dict):
        raise HTTPException(409, 'APPROVED_RUNTIME_SOURCE_REQUIRED')
    binding = config['binding']
    if (config.get('approved') is not True or binding.get('expected_account') != account
            or binding.get('region') != 'us-west-2'):
        raise HTTPException(409, 'APPROVED_RUNTIME_SOURCE_REQUIRED')
    sources = config.get('runtime_model_routes', [])
    if not isinstance(sources, list):
        raise HTTPException(409, 'APPROVED_RUNTIME_SOURCE_REQUIRED')
    matches = []
    try:
        for source in sources:
            validate_source(source, account, 'us-west-2')
            if model.id in source['exposure']:
                matches.append(source)
        if len(matches) != 1:
            raise ValueError('Ambiguous or absent Runtime source')
        return foundation_model(control, matches[0], model)
    except (ValueError, KeyError, TypeError):
        raise HTTPException(409, 'CURRENT_APPROVED_RUNTIME_BINDING_REQUIRED') from None


def runtime_model_config(model):
    return (model.protocol == 'messages-passthrough'
            or getattr(model, 'transport', 'inference-provider') == 'runtime-passthrough')


def registered_model(source, selected):
    """One reviewed source per model in this slice. Never replace its model by a dropdown ID."""
    cfg = load_config(source['config'], digest(source['config']))
    if cfg.model.id != selected:
        raise HTTPException(409, 'REGISTERED_MODEL_REQUIRED')
    if runtime_model_config(cfg.model):
        if source['platform'].get('model') != cfg.model.model_dump(mode='json'):
            raise HTTPException(409, 'REGISTERED_RUNTIME_MODEL_BINDING_REQUIRED')
    if cfg.model.protocol == 'messages-passthrough':
        # The pure codec permits 1..256. This product's reviewed reservation and
        # policy envelope is deliberately exactly one 256-output-token call.
        if (cfg.limits.maxOutputTokens != 256 or cfg.limits.maxIterations != 1
                or cfg.limits.maxModelCalls != 1 or cfg.limits.maxToolCalls != 0
                or cfg.tools or cfg.allowedTools or cfg.skills):
            raise HTTPException(409, 'OPUS5_ONE_CALL_TEXT_LIMITS_REQUIRED')


class FinalizeFoundation(Strict):
    agent_id: str
    version: int = Field(ge=1, strict=True)
    definition_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    approval_revision: int = Field(ge=1, strict=True)
    package_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    artifact_version: str = Field(min_length=1, max_length=1024)
    request_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{8,100}$')


LINUX_TARGET = 'linux-arm64-python3.13'


def linux_validation(db, binding):
    """Only independently protected execution evidence, never a request boolean."""
    proof = runs.get(db, 'foundation-linux:' + binding['package_digest']) or {}
    expected = {k: binding[k] for k in ('package_digest', 'manifest_digest',
                'admission_digest', 'artifact_source_digest', 'artifact_version', 'target')}
    passed = (all(proof.get(k) == v for k, v in expected.items())
              and proof.get('status') == 'PASS' and proof.get('execution') == 'ACTUAL_LINUX'
              and bool(proof.get('validator_identity')) and bool(proof.get('evidence_digest'))
              and proof.get('entrypoint_passed') is True)
    return {'target': LINUX_TARGET, 'status': 'PASS' if passed else 'UNVERIFIED'}


def finalized_artifact(db, approved):
    """Jobs consume a separately immutable binding, not hand-added approval fields."""
    binding = runs.get(db, 'foundation-artifact:' + approved['definition_digest'])
    if not binding or binding.get('approval_digest') != digest(approved):
        raise HTTPException(503, 'VERIFIED_ARTIFACT_FINALIZATION_REQUIRED')
    source = runs.get(db, 'foundation-source:' + approved.get('foundation_id', ''))
    if binding.get('source_record_digest') != digest(source):
        raise HTTPException(503, 'CURRENT_REGISTERED_ARTIFACT_SOURCE_REQUIRED')
    if binding.get('deployment_digest') != digest(runs.get(db, 'foundation-deployment')):
        raise HTTPException(503, 'CURRENT_ARTIFACT_DEPLOYMENT_REQUIRED')
    from .foundation_producer import composition_valid
    if linux_validation(db, binding)['status'] != 'PASS' and not composition_valid(db, binding):
        raise HTTPException(503, 'LINUX_EXECUTION_NOT_READY')
    deployment = runs.get(db, 'foundation-deployment') or {}
    return {**approved, **{k: deployment[k] for k in ('network', 'reservation_usd', 'cost_envelope')
                          if k in deployment},
            **{k: binding[k] for k in ('package_digest', 'artifact_key',
            'artifact_version', 'artifact_source_digest')}}


def verify_final_artifact(approved, data, settings):
    """Read exact existing object; reproduce complete locked ZIP and compare bytes.

    No upload, Runtime creation, credentials, grants or Dynamo writes here. The
    operator's existing private artifact bucket is selected from protected state.
    The existing packager rechecks CFN/IAM and protected approval, including receipt.
    """
    import hashlib
    import subprocess
    import tempfile
    from pathlib import Path
    import boto3
    from botocore.config import Config
    from scripts.package_foundation import save_config, package, dependency_command
    from .foundation_jobs import ArtifactReadback
    if (not settings or not settings.get('bucket') or approved['role'] not in settings.get('roles', [])
            or settings.get('network', {}).get('networkMode') != 'VPC'):
        raise HTTPException(409, 'REVIEWED_FOUNDATION_DEPLOYMENT_REQUIRED')
    if data.artifact_version == 'null':
        raise HTTPException(409, 'IMMUTABLE_OBJECT_VERSION_REQUIRED')
    key = 'releases/' + data.package_digest + '/foundation.zip'
    s3 = boto3.Session(region_name=settings['region']).client('s3', config=Config(
        retries={'total_max_attempts': 1}, connect_timeout=5, read_timeout=30))
    bucket = settings['bucket']
    if (s3.get_bucket_versioning(Bucket=bucket).get('Status') != 'Enabled'
            or not all(s3.get_public_access_block(Bucket=bucket)['PublicAccessBlockConfiguration'].get(k) is True
                       for k in ('BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy', 'RestrictPublicBuckets'))):
        raise HTTPException(409, 'PRIVATE_VERSIONED_ARTIFACT_BUCKET_REQUIRED')
    candidate = {**approved, 'artifact_key': key, 'artifact_version': data.artifact_version,
                 'package_digest': data.package_digest}
    ArtifactReadback(s3, bucket)(candidate)
    # Deterministic reconstruction covers ALL source/admission/dependency bytes,
    # not just ZIP marker files or the submitter's claimed package hash.
    with tempfile.TemporaryDirectory(prefix='foundation-finalize-') as directory:
        root = Path(directory)
        saved = save_config(approved['config'], root / 'manifests')
        subprocess.run(dependency_command(root / 'deps'), check=True, timeout=90,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        package(saved, root / 'complete.zip', admission=approved['admission'],
                approved=approved, dependencies=root / 'deps', mode='live')
        if hashlib.sha256((root / 'complete.zip').read_bytes()).hexdigest() != data.package_digest:
            raise HTTPException(409, 'COMPLETE_LOCKED_PACKAGE_DIGEST_REQUIRED')
    return key


def finalize_artifact(db, actor, data, owner, platform, *, verifier=verify_final_artifact):
    """One serializable verify-and-bind operation, including idempotent replay.

    Caller holds store.tx(): the global revision CAS fences every read (including
    current grants/approval) against revocation during object readback/build.
    Network effects are read-only, so a conflict/retry cannot duplicate uploads.
    """
    admin(actor)
    return _finalize_artifact(db, actor, data, owner, platform, verifier=verifier)


def _finalize_artifact(db, actor, data, owner, platform, *, verifier):
    """Internal binding primitive; only admin wrapper or authenticated queue producer."""
    from .app import get_version, validate_definition, resource, digest as definition_hash, audit
    from scripts.package_foundation import source_digest
    definition = get_version(db, data.agent_id, data.version)
    approved = runs.get(db, 'foundation-approved:' + data.definition_digest)
    agent = db.select('agents', where=[('id', '=', data.agent_id)]).fetchone()
    if (not approved or definition['digest'] != data.definition_digest
            or definition_hash({k:v for k,v in definition.items() if k != 'digest'}) != data.definition_digest
            or not agent or agent['current_version'] != data.version
            or (agent['owner'], agent['workspace']) != (owner['id'], owner['workspace'])
            or (approved['owner'], approved['workspace']) != (owner['id'], owner['workspace'])
            or owner['role'] != 'business' or actor['id'] == owner['id']):
        raise HTTPException(409, 'CURRENT_IMMUTABLE_OWNER_DEFINITION_REQUIRED')
    foundation = validate_definition(db, owner, definition)
    source = runs.get(db, 'foundation-source:' + definition['foundation_id']) or {}
    receipt = approved.get('receipt') or {}
    automatic = receipt.get('provenance') in ('policy-admission', 'human-exception')
    if automatic:
        from .self_service_admission import check_current
        check_current(db, owner, definition, approved)
    if (approved['revision'] != data.approval_revision or (not automatic and approved['expires_at'] <= time.time())
            or approved['policy_version'] != runs.get(db, 'policy')['version']
            or approved['epoch'] != (runs.get(db, 'foundation-epoch') or 0)
            or approved['source_revision'] != source.get('revision')
            or source.get('platform') != platform or source.get('foundation_digest') != digest(foundation)
            or approved['artifact_source_digest'] != source_digest()
            or approved['manifest_digest'] != digest(approved['config'])
            or approved['admission'] != admission_config(approved['config'], platform['endpoint'], platform['role'])
            or receipt.get('definition_digest') != data.definition_digest
            or (not automatic and (receipt.get('approver_role') != 'admin' or not receipt.get('approver')
                or receipt != runs.get(db, 'foundation-review:' + receipt.get('request_id', ''))))
            or any(source.get('catalog', {}).get(i) != digest(resource(db, 'components', i))
                   for i in [definition['model_id'], *definition['tools'], *definition['skills']])):
        raise HTTPException(409, 'CURRENT_PROTECTED_APPROVAL_REQUIRED')
    settings = runs.get(db, 'foundation-deployment')
    key = 'foundation-artifact:' + data.definition_digest
    request_key = 'foundation-finalize:' + data.request_id
    request_digest = digest(data.model_dump())
    old, replay = runs.get(db, key), runs.get(db, request_key)
    if old:
        if (old['request_digest'] != request_digest or old['actor'] != actor['id']
                or old['approval_digest'] != digest(approved) or replay != old
                or old['deployment_digest'] != digest(settings)):
            raise HTTPException(409, 'IMMUTABLE_ARTIFACT_BINDING_CONFLICT')
        binding = old
    else:
        if replay:
            raise HTTPException(409, 'FINALIZATION_REPLAY_CONFLICT')
        artifact_key = verifier(approved, data, settings)
        if artifact_key != 'releases/' + data.package_digest + '/foundation.zip':
            raise HTTPException(409, 'CONTENT_ADDRESSED_ARTIFACT_REQUIRED')
        binding = {'definition_digest': data.definition_digest, 'approval_digest': digest(approved),
                   'approval_revision': approved['revision'], 'deployment_digest': digest(settings),
                   'source_record_digest': digest(source),
                   'manifest_digest': approved['manifest_digest'],
                   'admission_digest': digest(approved['admission']),
                   'artifact_source_digest': approved['artifact_source_digest'],
                   'package_digest': data.package_digest, 'artifact_key': artifact_key,
                   'artifact_version': data.artifact_version, 'target': LINUX_TARGET,
                   'actor': actor['id'], 'request_digest': request_digest, 'created_at': time.time()}
        runs.put(db, key, binding)
        runs.put(db, request_key, binding)
        audit(db, actor['id'], 'foundation_artifact_finalized', data.agent_id, json.dumps(binding))
    validation = linux_validation(db, binding)
    return {'binding': binding, 'linux_validation': validation,
            'status': 'ARTIFACT_VERIFIED' if validation['status'] == 'PASS' else 'NOT_READY',
            'production_ready': False}
