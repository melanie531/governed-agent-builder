"""Exact diagnostic state operations behind a separately reviewed AWS_IAM route.

Only this service has repository credentials. No table/key/action interface and
no authority writer or inference transport is exposed to the Runtime.
"""
from contextlib import contextmanager
import re
import time

from foundation_harness.config import canonical, digest
from .diagnostic_capture import DiagnosticAdmission, PREFIX, PURPOSE, require, verify_runtime
from .foundation_runs import get, put
from scripts.opus_capture_ticket import reserve_capture, consume_capture, finish_capture


class _TransactionStore:
    """Reuse frozen ticket helpers without committing outside the outer tx."""
    def __init__(self, db):
        self.db = db

    @contextmanager
    def tx(self):
        yield self.db


def exchange(store, *, principal_arn, body, control, clock=time.time):
    require(isinstance(body, dict), 'CAPTURE_EXCHANGE_SHAPE_DENIED')
    operation = body.get('operation')
    fields = {'capture_ref', 'manifest_digest', 'operation'}
    if operation in {'claim', 'complete'}:
        fields.add('binding_digest')
    if operation == 'complete':
        fields.add('outcome')
        if body.get('outcome') == 'CAPTURED':
            fields.add('observation')
    require(operation in {'reserve', 'claim', 'complete'} and set(body) == fields,
            'CAPTURE_EXCHANGE_SHAPE_DENIED')
    match = re.fullmatch(r'arn:aws:sts::(\d{12}):assumed-role/([^/]+)/[^/]+', principal_arn or '')
    require(match is not None, 'CAPTURE_AUTHENTICATED_WORKLOAD_REQUIRED')
    role = f'arn:aws:iam::{match[1]}:role/{match[2]}'
    admission = DiagnosticAdmission(body['capture_ref'], role, clock)
    ticket = body['capture_ref']
    with store.tx() as db:
        resolved = admission.resolve(db)
        require(body['manifest_digest'] == resolved['runtime']['manifest_digest'],
                'CAPTURE_MANIFEST_BINDING_DENIED')
    if operation in {'reserve', 'claim'}:
        verify_runtime(control, resolved['runtime'])
    # Revalidate after control reads, and commit state+binding/evidence together.
    with store.tx() as db:
        resolved = admission.resolve(db)
        binding_digest = digest(resolved)
        bound = {'capture_ref': ticket, 'manifest_digest': body['manifest_digest'],
                 'binding_digest': binding_digest, 'role': role, 'operation': operation}
        unit = _TransactionStore(db)
        if operation == 'reserve':
            reserve_capture(unit, ticket, role=role, workspace=resolved['workspace'],
                request_digest=resolved['request_digest'], deadline=resolved['deadline'], now=clock(),
                costs=resolved['costs'], cap_usd=resolved['cap_usd'], admission_check=admission,
                budget_scopes=('account', 'workspace:' + resolved['workspace']))
            require(get(db, PREFIX + 'binding:' + ticket) is None, 'CAPTURE_BINDING_EXISTS')
            put(db, PREFIX + 'binding:' + ticket, {'digest': binding_digest})
            return {**bound, 'request': resolved['request'], 'deadline': resolved['deadline']}
        require(body['binding_digest'] == binding_digest
                and get(db, PREFIX + 'binding:' + ticket) == {'digest': binding_digest},
                'CAPTURE_EXCHANGE_BINDING_DENIED')
        if operation == 'claim':
            row = consume_capture(unit, ticket, role=role, workspace=resolved['workspace'],
                request_digest=resolved['request_digest'], now=clock(), admission_check=admission)
            return {**bound, 'deadline': row['deadline']}
        row = get(db, 'opus-capture:' + ticket)
        require(row is not None and (row['role'], row['workspace'], row['request_digest'],
                row.get('admission_identity')) == (role, resolved['workspace'], resolved['request_digest'],
                {'user_id': resolved['user_id'], 'agent_id': resolved['agent_id']}),
                'CAPTURE_EXCHANGE_BINDING_DENIED')
        outcome = body['outcome']
        require(outcome in {'CAPTURED', 'UNKNOWN'}, 'CAPTURE_OUTCOME_INVALID')
        receipt = None
        if outcome == 'CAPTURED':
            observation = body['observation']
            require(isinstance(observation, dict) and set(observation) == {'response_digest', 'observed_model'}
                    and isinstance(observation['response_digest'], str)
                    and re.fullmatch('[a-f0-9]{64}', observation['response_digest'])
                    and (observation['observed_model'] is None or
                         isinstance(observation['observed_model'], str) and len(observation['observed_model']) <= 200),
                    'CAPTURE_EVIDENCE_SHAPE_DENIED')
            receipt = {'capture_ref': ticket, 'outcome': outcome, 'purpose': PURPOSE,
                **observation, 'request_digest': resolved['request_digest'],
                'runtime_ref': resolved['runtime_ref'], 'held_usd': resolved['cap_usd']}
            require(len(canonical(receipt)) <= 4096, 'CAPTURE_RECEIPT_BOUND')
            require(get(db, PREFIX + 'evidence:' + ticket) is None, 'CAPTURE_EVIDENCE_EXISTS')
        finish_capture(unit, ticket, outcome=outcome)
        if receipt is not None:
            put(db, PREFIX + 'evidence:' + ticket, receipt)
        # Observations are unverified Runtime evidence, never model approval.
        return {**bound, 'outcome': outcome, 'receipt': receipt}
