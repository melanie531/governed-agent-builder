"""Server-owned Foundation policy and immutable domain admission receipts.

All mutations run inside store.tx(): SQLite serialization / Dynamo global CAS.
No client-selected identity, approval state, limits, endpoint or role is accepted.
"""
import copy
import time
import uuid
from typing import Literal

from fastapi import HTTPException
from pydantic import Field
from .schemas import Strict
from . import foundation_runs as runs
from .foundation_approval import admin
from foundation_harness.config import digest, load_config
from foundation_harness.package_admission import admission_config


class FoundationPolicy(Strict):
    foundation_id: str
    source_revision: int = Field(ge=1, strict=True)
    expected_revision: int = Field(ge=0, strict=True)
    workspaces: list[str] = Field(min_length=1)
    allowed_capabilities: list[str] = Field(min_length=1)
    data_sources: list[Literal['synthetic-local-only', 'approved-public-web']] = Field(min_length=1)
    max_dataset_cases: int = Field(ge=1, le=20, strict=True)
    max_prompt_chars: int = Field(ge=10, le=8000, strict=True)
    incompatible_pairs: list[tuple[str, str]] = Field(default_factory=list)
    reason: str = Field(min_length=10, max_length=1000)


class AdmissionRequest(Strict):
    version: int = Field(ge=1, strict=True)


class ExceptionRequest(Strict):
    version: int = Field(ge=1, strict=True)
    reason: str = Field(min_length=10, max_length=1000)


class ExceptionDecision(Strict):
    approve: bool
    reason: str = Field(min_length=10, max_length=1000)


def approve_policy(db, actor, data):
    admin(actor)
    from .app import resource, audit
    foundation = resource(db, 'foundations', data.foundation_id)
    source = runs.get(db, 'foundation-source:' + data.foundation_id)
    if (not foundation['approved'] or not source or source['revision'] != data.source_revision
            or source['foundation_digest'] != digest(foundation)
            or not set(data.allowed_capabilities) <= set(source['catalog'])):
        raise HTTPException(409, 'CURRENT_APPROVED_FOUNDATION_SOURCE_REQUIRED')
    key = 'foundation-policy:' + data.foundation_id
    previous = runs.get(db, key) or {}
    if previous.get('revision', 0) != data.expected_revision:
        raise HTTPException(409, 'FOUNDATION_POLICY_REVISION_CONFLICT')
    record = {**data.model_dump(exclude={'expected_revision'}), 'revision': data.expected_revision + 1,
              'source_digest': digest(source), 'foundation_digest': digest(foundation),
              'approver': actor['id'], 'provenance': 'foundation-policy', 'approved_at': time.time()}
    runs.put(db, key, record)
    runs.put(db, key + ':' + str(record['revision']), record)
    # Invalidate in-flight jobs, including policy -> revoke -> restore sequences.
    runs.put(db, 'foundation-epoch', (runs.get(db, 'foundation-epoch') or 0) + 1)
    audit(db, actor['id'], 'foundation_policy_approved', data.foundation_id, digest(record))
    return record


def current_definition(db, actor, agent_id, version):
    from .app import agent_access, get_version, digest as definition_hash
    if actor.get('role') != 'business':
        raise HTTPException(403, 'BUSINESS_MEMBERSHIP_REQUIRED')
    agent = agent_access(db, actor, agent_id)
    if agent['current_version'] != version:
        raise HTTPException(409, 'CURRENT_DOMAIN_VERSION_REQUIRED')
    definition = get_version(db, agent_id, version)
    if (definition['owner'], definition['workspace']) != (actor['id'], actor['workspace']):
        raise HTTPException(403, 'OWNER_WORKSPACE_REQUIRED')
    if definition['digest'] != definition_hash({k: v for k, v in definition.items() if k != 'digest'}):
        raise HTTPException(409, 'IMMUTABLE_DEFINITION_REQUIRED')
    return definition


def evaluate_policy(db, actor, definition):
    """Mandatory structural/data/compatibility checks, not an LLM approval guess.

    An injected evaluator can add semantic risk checks, never bypass these gates.
    Data source labels are constrained by DefinitionInput; they are not DLP proof.
    """
    from .app import validate_definition, resource
    from scripts.package_foundation import source_digest
    foundation = validate_definition(db, actor, definition)
    source = runs.get(db, 'foundation-source:' + definition['foundation_id']) or {}
    policy = runs.get(db, 'foundation-policy:' + definition['foundation_id']) or {}
    if (not policy or policy['source_digest'] != digest(source)
            or policy['foundation_digest'] != digest(foundation)
            or source.get('foundation_digest') != digest(foundation)
            or source.get('config', {}).get('foundation', {}).get('digest') != source_digest()):
        raise HTTPException(403, 'FOUNDATION_POLICY_APPROVAL_REQUIRED')
    selected = [definition['model_id'], *definition['tools'], *definition['skills']]
    if any(source['catalog'].get(i) != digest(resource(db, 'components', i)) for i in selected):
        raise HTTPException(409, 'CURRENT_CATALOG_BINDING_REQUIRED')
    if actor['workspace'] not in policy['workspaces']:
        raise HTTPException(403, 'WORKSPACE_POLICY_DENIED')
    if (not set(selected) <= set(policy['allowed_capabilities'])
            or definition['source'] not in policy['data_sources']):
        raise HTTPException(403, 'HUMAN_EXCEPTION_REQUEST_REQUIRED')
    if (len(definition['dataset']) > policy['max_dataset_cases']
            or len(definition['prompt']) > policy['max_prompt_chars']
            or any(set(pair) <= set(selected) for pair in policy['incompatible_pairs'])):
        raise HTTPException(403, 'RISK_OR_COMPATIBILITY_POLICY_DENIED')
    if definition['output_format'] not in foundation['config_schema']['format']:
        raise HTTPException(403, 'TEMPLATE_CONTRACT_DENIED')
    return source, policy


def eligibility(db, actor, definition, source, policy):
    from .app import resource
    selected = [definition['model_id'], *definition['tools'], *definition['skills']]
    return {'creator': actor['id'], 'workspace': actor['workspace'], 'role': actor['role'],
            'external_allowed': actor.get('external_allowed', False),
            'definition_digest': definition['digest'], 'agent_id': definition['agent_id'],
            'version': definition['version'], 'prompt_digest': digest(definition['prompt']),
            'dataset_digest': digest(definition['dataset']), 'eval_digest': digest(definition['rubric']),
            'component_versions': definition['component_versions'],
            'catalog_digest': digest([resource(db, 'components', i) for i in selected]),
            'foundation_approval_revision': policy['revision'], 'foundation_policy_digest': digest(policy),
            'source_revision': source['revision'], 'source_record_digest': digest(source),
            'grant_epoch': runs.get(db, 'foundation-epoch') or 0,
            'policy_digest': digest(runs.get(db, 'policy')),
            'policy_version': runs.get(db, 'policy')['version']}


def admit(db, actor, agent_id, version, *, evaluator=None):
    """Evaluate on every call. Receipt immutable; expiring authority is separate."""
    from .app import audit
    definition = current_definition(db, actor, agent_id, version)
    source, policy = evaluate_policy(db, actor, definition)
    if evaluator is not None and evaluator(db, actor, definition, policy) is not True:
        raise HTTPException(403, 'ADDITIONAL_RISK_REVIEW_REQUIRED')
    binding = eligibility(db, actor, definition, source, policy)
    key = 'foundation-approved:' + definition['digest']
    old = runs.get(db, key)
    if old:
        if old.get('receipt', {}).get('provenance') not in ('policy-admission', 'human-exception'):
            raise HTTPException(409, 'LEGACY_M0_REQUIRES_NEW_DOMAIN_VERSION')
        if old['receipt']['eligibility'] != binding:
            raise HTTPException(409, 'ADMISSION_STALE_REVISE_AND_RETEST')
        if old['receipt'] != runs.get(db, 'domain-admission:' + old['receipt']['id']):
            raise HTTPException(403, 'IMMUTABLE_ADMISSION_RECEIPT_REQUIRED')
        result = old
    else:
        raw = copy.deepcopy(source['config'])
        if raw['model']['id'] != definition['model_id']:
            raise HTTPException(409, 'REGISTERED_MODEL_REQUIRED')
        tools = dict(zip(source['tool_ids'], raw['tools']))
        skills = {s['id']: s for s in raw['skills']}
        raw.update(name='agent_' + agent_id, version=str(version),
                   systemPrompt=[{'text': definition['prompt']}],
                   tools=[tools[i] for i in definition['tools']], skills=[skills[i] for i in definition['skills']])
        raw['allowedTools'] = [t['name'] for t in raw['tools']]
        for field in ('dataset', 'rubric'):
            raw['evaluation'][field] = {'id': field, 'version': str(version), 'digest': digest(definition[field])}
        load_config(raw, digest(raw))
        now = time.time()
        exception = runs.get(db, 'domain-exception-approved:' + definition['digest'])
        # Exception is provenance, NEVER a bypass of current foundation/grants/policy.
        provenance = 'human-exception' if exception and exception['eligibility'] == binding else 'policy-admission'
        receipt = {'id': uuid.uuid4().hex, 'provenance': provenance, 'eligibility': binding,
                   'created_at': now, 'creator': actor['id'], 'definition_digest': definition['digest'],
                   'exception_request': exception['id'] if provenance == 'human-exception' else None}
        result = {'revision': 1, 'definition_digest': definition['digest'], 'owner': actor['id'],
                  'workspace': actor['workspace'], 'config': raw, 'manifest_digest': digest(raw),
                  'artifact_source_digest': raw['foundation']['digest'], 'role': source['platform']['role'],
                  'admission': admission_config(raw, source['platform']['endpoint'], source['platform']['role']),
                  'tool_ids': definition['tools'], 'epoch': binding['grant_epoch'],
                  'policy_version': binding['policy_version'], 'expires_at': now + 3600,
                  'runtime_admission_reviewed': True, 'receipt': receipt,
                  'source_revision': source['revision'], 'foundation_id': definition['foundation_id']}
        runs.put(db, key, result)
        runs.put(db, 'domain-admission:' + receipt['id'], receipt)
        audit(db, actor['id'], 'domain_policy_admission', agent_id, receipt['id'])
    # Revalidation refreshes request authority, NEVER mutates a finalized receipt.
    runs.put(db, 'domain-authority:' + definition['digest'], {
        'receipt_digest': digest(result['receipt']), 'eligibility': binding, 'expires_at': time.time() + 3600})
    return result


def check_current(db, actor, definition, approved):
    """Read-only worker/call check: never renew an expired call authority."""
    current_definition(db, actor, definition['agent_id'], definition['version'])
    source, policy = evaluate_policy(db, actor, definition)
    binding = eligibility(db, actor, definition, source, policy)
    receipt = approved.get('receipt', {})
    authority = runs.get(db, 'domain-authority:' + definition['digest']) or {}
    if receipt.get('provenance') == 'human-exception':
        exception = runs.get(db, 'domain-exception:' + receipt.get('exception_request', '')) or {}
        if exception.get('status') != 'APPROVED' or exception.get('eligibility') != binding:
            raise HTTPException(403, 'CURRENT_HUMAN_EXCEPTION_REQUIRED')
    if (receipt.get('provenance') not in ('policy-admission', 'human-exception')
            or receipt.get('eligibility') != binding
            or runs.get(db, 'domain-admission:' + receipt.get('id', '')) != receipt
            or authority.get('receipt_digest') != digest(receipt)
            or authority.get('eligibility') != binding or authority.get('expires_at', 0) <= time.time()):
        raise HTTPException(403, 'CURRENT_POLICY_ADMISSION_REQUIRED')


def request_exception(db, actor, agent_id, data):
    definition = current_definition(db, actor, agent_id, data.version)
    from .app import audit
    recent = [r for r in db.select('audit', where=[('actor', '=', actor['id']),
              ('action', '=', 'domain_exception_requested'), ('created', '>', time.time() - 3600)])]
    if len(recent) >= 30:
        raise HTTPException(429, 'EXCEPTION_REQUEST_BUDGET')
    record = {'id': uuid.uuid4().hex, 'creator': actor['id'], 'workspace': actor['workspace'],
              'agent_id': agent_id, 'version': data.version, 'definition_digest': definition['digest'],
              'reason': data.reason, 'status': 'PENDING', 'created_at': time.time(),
              'expires_at': time.time() + 3600}
    runs.put(db, 'domain-exception:' + record['id'], record)
    audit(db, actor['id'], 'domain_exception_requested', record['id'])
    return record


def decide_exception(db, actor, request_id, data, owner):
    admin(actor)
    from .app import audit
    record = runs.get(db, 'domain-exception:' + request_id)
    if not record or record['status'] != 'PENDING' or record['expires_at'] <= time.time():
        raise HTTPException(409, 'CURRENT_PENDING_EXCEPTION_REQUIRED')
    if record['creator'] != owner['id'] or actor['id'] == owner['id']:
        raise HTTPException(403, 'INDEPENDENT_EXCEPTION_REVIEW_REQUIRED')
    definition = current_definition(db, owner, record['agent_id'], record['version'])
    if definition['digest'] != record['definition_digest']:
        raise HTTPException(409, 'EXACT_EXCEPTION_DEFINITION_REQUIRED')
    if data.approve:
        # Admin must first approve source/capability/data/risk policy and grants.
        # A decision cannot mint arbitrary tool access or suppress hard guards.
        source, policy = evaluate_policy(db, owner, definition)
        record['eligibility'] = eligibility(db, owner, definition, source, policy)
    record.update(status='APPROVED' if data.approve else 'REJECTED', approver=actor['id'], decision=data.reason)
    runs.put(db, 'domain-exception:' + request_id, record)
    if data.approve:
        runs.put(db, 'domain-exception-approved:' + definition['digest'], record)
    audit(db, actor['id'], 'domain_exception_decided', request_id, record['status'])
    return record
