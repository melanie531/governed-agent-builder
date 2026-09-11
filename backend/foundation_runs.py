"""Server-owned run ledger on the Studio serializable repository/DynamoDB fence.

Reservations and jobs commit together, so a DynamoDB stream cannot dispatch an
unreserved job. Unknown outcomes never release money. No identity comes from a
Runtime payload. The separate IAM exchange must be deployed/reviewed before use.
"""
import json
import time
from dataclasses import asdict
from decimal import Decimal

from foundation_harness.context import Binding, Denied


def get(db, key):
    row = db.select('settings', where=[('key', '=', key)]).fetchone()
    return json.loads(row['body']) if row else None


def put(db, key, value):
    db.insert('settings', {'key': key, 'body': json.dumps(value)}, upsert=True)


def reserve(db, *, job_id, definition, persona, approved, epoch, deadline):
    key = 'foundation-run:' + job_id
    existing = get(db, key)
    if existing:
        if existing['definition_digest'] != definition['digest']:
            raise Denied('RESERVATION_IDEMPOTENCY_CONFLICT')
        return existing
    amount = Decimal(approved['reservation_usd'])
    if not amount.is_finite() or not Decimal('0') < amount <= Decimal('5'):
        raise Denied('RESERVATION_LIMIT')
    # Lifetime account self-cap, deliberately not reset by day/job/user/retry.
    buckets = ['account', 'agent:' + definition['agent_id'], 'user:' + persona['id']]
    for scope in buckets:
        ledger = get(db, 'foundation-budget:' + scope) or {'held_usd': '0', 'estimated_usd': '0'}
        held = Decimal(ledger['held_usd']) + amount
        if held + Decimal(ledger['estimated_usd']) > Decimal('5'):
            raise Denied('OVERNIGHT_USD_CAP')
        put(db, 'foundation-budget:' + scope, {**ledger, 'held_usd': str(held)})
    row = {'run_ref': job_id, 'definition_digest': definition['digest'],
           'agent': definition['agent_id'], 'version': definition['version'],
           'owner': persona['id'], 'workspace': persona['workspace'],
           'hosted': definition.get('mode') == 'CLOUD-HOSTED DEMO',
           'epoch': epoch, 'deadline': deadline, 'reservation_usd': str(amount),
           'limits': approved['config']['limits'], 'manifest_digest': approved['manifest_digest'],
           'foundation_digest': approved['config']['foundation']['digest'],
           'runtime_role': approved['role'], 'runtime': None, 'state': 'RESERVED',
           'calls': {}, 'usage': None, 'billing_estimate_usd': None,
           'actual_invoice_usd': None, 'buckets': buckets, 'settled': False}
    put(db, key, row)
    return row


def current(db, row):
    if row['deadline'] <= time.time():
        raise Denied('AUTHORITY_EXPIRED')
    agent = db.select('agents', where=[('id', '=', row['agent'])]).fetchone()
    if not agent or (agent['owner'], agent['workspace'], agent['current_version']) != (
            row['owner'], row['workspace'], row['version']):
        raise Denied('CURRENT_DEFINITION_DENIED')
    # Every capability/grant and policy change invalidates pending admissions.
    if ((get(db, 'foundation-epoch') or 0) != row['epoch']
            or get(db, 'policy')['version'] != row['approved']['policy_version']):
        raise Denied('CURRENT_GRANT_DENIED')
    version = db.select('versions', where=[('agent', '=', row['agent']), ('version', '=', row['version'])]).fetchone()
    if not version or version['digest'] != row['definition_digest']:
        raise Denied('DEFINITION_DIGEST_DENIED')
    principal = db.select('principals', where=[('id', '=', row['owner'])]).fetchone()
    if row.get('hosted') and not principal:
        raise Denied('MEMBERSHIP_EXPIRED')
    if principal:
        persona = json.loads(principal['body'])
        if principal['expires'] <= time.time() or persona['workspace'] != row['workspace']:
            raise Denied('MEMBERSHIP_EXPIRED')
    # Independently recheck current catalog/grants, not only the epoch counter.
    from .app import validate_definition
    from .catalog import PERSONAS
    persona = json.loads(principal['body']) if principal else PERSONAS.get(row['owner'])
    if not persona:
        raise Denied('MEMBERSHIP_REQUIRED')
    validate_definition(db, persona, json.loads(version['body']))
    approved = get(db, 'foundation-approved:' + row['definition_digest'])
    if approved != row['approved'] or approved['expires_at'] <= time.time():
        raise Denied('APPROVED_ARTIFACT_REVOKED')
    session_auth = db.select('job_authority', where=[('id', '=', row['run_ref'])]).fetchone() if principal else None
    if principal:
        session = db.select('hosted_sessions', where=[('id_hash', '=', session_auth['session_hash'])]).fetchone() if session_auth else None
        if not session or session['expires'] <= time.time() or session['subject'] != row['owner']:
            raise Denied('SESSION_REVOKED')


def bind_runtime(db, row, runtime):
    if row['runtime'] and row['runtime'] != runtime:
        raise Denied('RUNTIME_REBIND_DENIED')
    row['runtime'] = runtime
    put(db, 'foundation-run:' + row['run_ref'], row)


def claim_dispatch(db, row):
    current(db, row)
    if row['state'] != 'RESERVED' or not row['runtime']:
        raise Denied('RUN_DISPATCH_REPLAY_DENIED')
    row['state'] = 'DISPATCHED'
    row['execution_expires'] = min(row['deadline'], time.time() + row['limits']['timeoutSeconds'])
    put(db, 'foundation-run:' + row['run_ref'], row)


def exchange(db, *, principal_arn, body):
    """principal_arn must come ONLY from API Gateway AWS_IAM authorizer.

    Exact assumed role sessions are normalized without trusting role/user headers.
    No public route may invoke this function; dedicated Lambda invoke permission
    must be limited to the exact API/stage/POST route before enabling live mode.
    """
    import re
    allowed = {'run_ref', 'manifest_digest', 'operation', 'call_id', 'usage'}
    if not isinstance(body, dict) or set(body) - allowed:
        raise Denied('EXCHANGE_SHAPE_DENIED')
    row = get(db, 'foundation-run:' + body.get('run_ref', ''))
    if not row or not row['runtime']:
        raise Denied('RUN_NOT_FOUND')
    match = re.fullmatch(r'arn:aws:sts::(\d{12}):assumed-role/([^/]+)/[^/]+', principal_arn or '')
    role = f'arn:aws:iam::{match[1]}:role/{match[2]}' if match else None
    if role != row['runtime_role'] or body.get('manifest_digest') != row['manifest_digest']:
        raise Denied('IAM_WORKLOAD_BINDING_DENIED')
    current(db, row)
    if row.get('execution_expires', 0) <= time.time():
        raise Denied('EXECUTION_EXPIRED')
    operation = body.get('operation')
    if operation == 'redeem':
        if row['state'] != 'DISPATCHED':
            raise Denied('RUN_REPLAY_DENIED')
        row['state'] = 'REDEEMED'
    elif operation == 'settle' and row['state'] == 'FINISHED' and body.get('usage') == row['usage']:
        pass
    elif row['state'] != 'REDEEMED':
        raise Denied('RUN_NOT_REDEEMED')
    elif operation in ('model', 'tool', 'gateway', 'export'):
        call_id = body.get('call_id')
        if not isinstance(call_id, str) or not re.fullmatch(r'[a-z]+-[1-9][0-9]?', call_id):
            raise Denied('CALL_ID_REQUIRED')
        # A replay returns a stable denial; never repeats a potentially paid call.
        key = operation + ':' + call_id
        if key in row['calls']:
            raise Denied('CALL_ALREADY_CLAIMED')
        cap = {'model': row['limits']['maxModelCalls'], 'tool': row['limits']['maxToolCalls'],
               'gateway': 8, 'export': 5}[operation]
        if sum(k.startswith(operation + ':') for k in row['calls']) >= cap:
            raise Denied('PERSISTENT_CALL_CAP')
        row['calls'][key] = 'CLAIMED'
    elif operation == 'settle':
        usage = body.get('usage')
        if usage is not None and (not isinstance(usage, dict) or set(usage) != {'input_tokens', 'output_tokens'}
                or any(type(v) is not int or v < 0 for v in usage.values())):
            raise Denied('USAGE_INVALID')
        row.update(state='FINISHED', usage=usage, settled=True)
        # Tokens alone do not price Gateway/Runtime/OTel. Hold the full reserve
        # until an independently measured all-service cost reconciliation exists.
    elif operation != 'authorize':
        raise Denied('OPERATION_DENIED')
    put(db, 'foundation-run:' + row['run_ref'], row)
    runtime = row['runtime']
    binding = Binding(row['run_ref'], row['owner'], row['workspace'], row['runtime_role'],
                      runtime['runtime_arn'], runtime['runtime_version'], row['run_ref'].ljust(33, '0'),
                      row['manifest_digest'], row['foundation_digest'], row['epoch'], row['execution_expires'])
    return {'binding': asdict(binding), 'stored_input': row.get('stored_input', ''),
            'limits': row['limits'], 'reservation_handle': row['run_ref'],
            'reservation_usd': row['reservation_usd']}
