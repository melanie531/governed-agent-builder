"""Backend-issued run admission and platform listing on the existing IAM exchange."""
from dataclasses import asdict
import re
import secrets
import time

from foundation_harness.config import digest
from foundation_harness.context import Binding
from . import journey_alpr as calls
from . import journey_alpr_workload as workloads
from .foundation_runs import get, put
from .journey import job_state

PREFIX = 'journey-alpr-run:'


def selected(db, journey, binding):
    state = job_state(db, binding.run_ref)
    calls.require(state, 'ALPR_RUN_NOT_ACTIVE')
    actor, definition = journey.authority(db, state)
    manifest = get(db, 'journey-manifest:' + definition['digest'])
    from .app import caller_scopes
    scopes = caller_scopes(db, actor, definition)
    tools = {}
    for tool in definition['tools']:
        if tool in calls.TOOLS:
            row = {'binding': asdict(binding), 'agent_id': definition['agent_id'],
                   'agent_version': definition['version'], 'tool': tool, 'scope': scopes.get(tool)}
            calls.current(db, journey, row)
            name = next((t['name'] for t in manifest['tools'] if t['name'].rsplit('___', 1)[-1] == tool), None)
            calls.require(name, 'ALPR_MANIFEST_TOOL_REQUIRED')
            tools[name] = tool
    calls.require(tools, 'ALPR_MANIFEST_TOOL_REQUIRED')
    return tools


def issue_run(db, journey, state, runtime, text, request_id, history=None):
    record = workloads.load(db, runtime.get('alpr_deployment'))
    actor, definition = journey.authority(db, state)
    manifest = get(db, 'journey-manifest:' + definition['digest'])
    a = record['a']
    calls.require((runtime['arn'], runtime['version'], runtime['manifest']['digest']) ==
                  (a['arn'], a['version'], record['manifest_digest'])
                  and digest(manifest) == record['manifest_digest'], 'ALPR_DEPLOYMENT_BINDING_DENIED')
    binding = Binding(state['id'], actor['id'], actor['workspace'], a['configuration']['roleArn'],
        a['arn'], a['version'], 'gab-' + request_id, digest(manifest), digest(manifest['foundation']), 0,
        min(state['deadline'], time.time() + 180))
    selected(db, journey, binding)
    key = PREFIX + 'session:' + digest([state['id'], binding.runtime_session])
    calls.require(get(db, key) is None, 'ALPR_RUN_REPLAY_DENIED')
    reference = secrets.token_urlsafe(32)
    row = {'binding': asdict(binding), 'deployment_ref': runtime['alpr_deployment'],
           'invocation_digest': digest({'input': text, 'request_id': request_id, 'history': history or []}),
           'state': 'ISSUED', 'principal': None, 'calls': []}
    put(db, PREFIX + digest(reference), row)
    put(db, key, digest(reference))
    return reference


def dispatch(db, journey, *, principal_arn, body, control, iam):
    calls.require(isinstance(body, dict), 'ALPR_EXCHANGE_SHAPE_DENIED')
    operation = body.get('operation')
    role = workloads.role_from_principal(principal_arn)
    if operation == 'platform-list':
        calls.require(set(body) == {'operation', 'runtime_arn', 'runtime_version', 'deployment_digest'},
                      'ALPR_EXCHANGE_SHAPE_DENIED')
        reference = body['deployment_digest']
        calls.require(isinstance(reference, str) and re.fullmatch(r'[a-f0-9]{64}', reference),
                      'ALPR_DEPLOYMENT_REQUIRED')
        record = get(db, 'journey-alpr-listing:' + reference)
        calls.require(record and not get(db, 'journey-alpr-listing:revoked:' + reference)
                      and role == record['b']['configuration']['roleArn']
                      and all(body[k] == record['specialist'][k] for k in
                              ('runtime_arn', 'runtime_version', 'deployment_digest')), 'ALPR_PLATFORM_LISTING_DENIED')
        workloads.verify_runtime(control, iam, record['b'], record['gateway_role'], purpose='b')
        return {'tools': list(calls.TOOLS), 'deployment_digest': reference}
    if operation not in {'resolve-run', 'authorize-run', 'issue-call', 'finish-run'}:
        # A production redemption always proves B against the same immutable
        # deployment used by A. Legacy issue() fixtures have no production entry.
        reference = body.get('reference')
        calls.require(isinstance(reference, str) and re.fullmatch(r'[A-Za-z0-9_-]{43}', reference),
                      'ALPR_CALLER_REFERENCE_REQUIRED')
        row = get(db, calls.PREFIX + reference)
        record = workloads.load(db, (row or {}).get('deployment_ref'))
        calls.require(role == record['b']['configuration']['roleArn'], 'ALPR_IAM_WORKLOAD_BINDING_DENIED')
        workloads.verify_runtime(control, iam, record['b'], record['gateway_role'], purpose='b')
        return calls.exchange(db, journey, principal_arn=principal_arn, body=body)
    fields = {'operation', 'run_reference'}
    fields |= {'invocation_digest'} if operation == 'resolve-run' else {'binding_digest'}
    if operation == 'authorize-run': fields |= {'action', 'resource'}
    if operation == 'issue-call': fields |= {'tool', 'arguments'}
    calls.require(set(body) == fields, 'ALPR_EXCHANGE_SHAPE_DENIED')
    reference = body['run_reference']
    calls.require(isinstance(reference, str) and re.fullmatch(r'[A-Za-z0-9_-]{43}', reference), 'ALPR_RUN_REFERENCE_REQUIRED')
    key = PREFIX + digest(reference)
    row = get(db, key)
    calls.require(row and row['state'] != 'FINISHED', 'ALPR_RUN_REPLAY_DENIED')
    binding = Binding(**row['binding'])
    calls.require(role == binding.workload, 'ALPR_IAM_WORKLOAD_BINDING_DENIED')
    record = workloads.load(db, row['deployment_ref'])
    calls.require((record['a']['arn'], record['a']['version'], record['a']['configuration']['roleArn'], record['manifest_digest']) ==
                  (binding.runtime, binding.runtime_version, binding.workload, binding.manifest_digest), 'ALPR_DEPLOYMENT_BINDING_DENIED')
    tools = selected(db, journey, binding)
    if operation != 'issue-call':
        workloads.verify(control, iam, record)
    if operation == 'resolve-run':
        calls.require(row['state'] == 'ISSUED' and body['invocation_digest'] == row['invocation_digest'], 'ALPR_RUN_REPLAY_OR_INPUT_DENIED')
        row.update(state='RESOLVED', principal=principal_arn)
    else:
        calls.require(row['state'] == 'RESOLVED' and row['principal'] == principal_arn
                      and body['binding_digest'] == digest(row['binding']), 'ALPR_ROLE_SESSION_BINDING_DENIED')
    result = {'binding': row['binding'], 'binding_digest': digest(row['binding'])}
    if operation == 'authorize-run':
        action, resource = body['action'], body['resource']
        calls.require((action in {'start', 'finish'} and resource == '') or
                      (action == 'tool' and resource in tools), 'ALPR_OPERATION_DENIED')
    if operation == 'issue-call':
        calls.require(body['tool'] in tools, 'ALPR_MANIFEST_TOOL_REQUIRED')
        def verify_issuer(db, bound, specialist):
            calls.require(bound == binding and specialist == record['specialist'],
                          'ALPR_DEPLOYMENT_BINDING_DENIED')
            workloads.verify(control, iam, workloads.load(db, row['deployment_ref']))
            return binding

        reference = calls.issue(db, journey, binding=binding, specialist=record['specialist'],
            tool=tools[body['tool']], arguments=body['arguments'],
            verify_workload=verify_issuer,
            deployment_ref=row['deployment_ref'])
        row['calls'].append(reference)
        result['reference'] = reference
    if operation == 'finish-run' or operation == 'authorize-run' and body['action'] == 'finish':
        calls.require(all(get(db, calls.PREFIX + ref)['state'] == 'FINISHED' for ref in row['calls']), 'ALPR_CALLS_INCOMPLETE')
        row['state'] = 'FINISHED'
    put(db, key, row)
    return result
