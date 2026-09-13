"""Send-path gate: STRICT ORDERING + actual send-count assertions (哥哥 sharpening).

Every case puts a SPY/COUNTER on the real dispatch entry (transport.post) and
asserts the ACTUAL number of sends: ==1 on the happy path, ==0 on every reject/
crash/timeout/concurrent-loser case. Checking ticket STATE alone is insufficient;
the assertion is on how many times the real send actually fired.

No live inference; the live call stays UNVERIFIED-pending-哥哥-IAM/Cedar-apply.
"""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from scripts.opus_capture_send import guarded_capture_send, SendGateDenied
from backend.store import Store
from backend.foundation_runs import get

ENDPOINT = ('https://gab-foundation-model-m0-example.gateway.bedrock-agentcore'
            '.us-west-2.amazonaws.com/bedrockrt/v1/messages')


def envelope():
    names = ['model_input', 'model_output', 'gateway_policy', 'runtime_lifetime', 'storage', 'telemetry']
    return {k: {'usd': '0.01', 'basis': 'synthetic-test-only'} for k in names}


class SendSpy:
    """Counts EVERY real dispatch. A live send would hit Bedrock here."""
    def __init__(self, behavior='fail'):
        self.count = 0
        self.behavior = behavior  # 'fail' | 'timeout' | 'ok'
        self.lock = threading.Lock()

    def post(self, endpoint, body, headers, timeout):
        with self.lock:
            self.count += 1
        if self.behavior == 'ok':
            return {'type': 'message', 'model': 'observed'}, {'http_status': 200}
        if self.behavior == 'timeout':
            raise TimeoutError('read timeout after IAM/Cedar not applied')
        raise RuntimeError('SignatureDoesNotMatch: IAM/Cedar not applied')


def send(store, spy, ticket='one', *, role='synthetic-role', workspace='synthetic-project',
         request_digest='a'*64, deadline=200, now=101, costs=None, cap_usd='0.06'):
    return guarded_capture_send(
        store, ticket, role=role, workspace=workspace, request_digest=request_digest,
        deadline=deadline, now=now, costs=envelope() if costs is None else costs,
        cap_usd=cap_usd, transport=spy, endpoint=ENDPOINT,
        body={'model': 'x', 'max_tokens': 8}, headers={})


# ---------------------------------------------------------------- happy path: 1 send
def test_happy_path_sends_exactly_once_after_atomic_claim(tmp_path):
    s = Store(str(tmp_path / 'c.sqlite')); spy = SendSpy('fail')
    out = send(s, spy)
    assert spy.count == 1                       # ACTUAL send fired exactly once
    assert out['dispatched'] is True
    assert out['held_usd'] == '0.06' and out['held_micros'] == 60000
    assert out['live_call_status'] == 'UNVERIFIED-pending-哥哥-apply'
    assert out['sets_ready_flag'] is False
    with s.tx() as db:
        assert get(db, 'opus-capture:one')['state'] == 'UNKNOWN'  # unknown => held retained


# ---------------------------------------------------- Defect 1: non-finite time => 0 sends
@pytest.mark.parametrize('bad_now', [float('inf'), float('-inf'), float('nan')])
def test_non_finite_now_zero_sends(tmp_path, bad_now):
    s = Store(str(tmp_path / 'c.sqlite')); spy = SendSpy('fail')
    with pytest.raises(SendGateDenied, match='TIME_VALUE_NOT_FINITE'):
        send(s, spy, now=bad_now)
    assert spy.count == 0                        # never dispatched
    with s.tx() as db:
        assert get(db, 'opus-capture:one') is None   # no ticket created


@pytest.mark.parametrize('bad_deadline', [float('inf'), float('-inf'), float('nan')])
def test_non_finite_deadline_zero_sends(tmp_path, bad_deadline):
    s = Store(str(tmp_path / 'c.sqlite')); spy = SendSpy('fail')
    with pytest.raises(SendGateDenied, match='TIME_VALUE_NOT_FINITE'):
        send(s, spy, deadline=bad_deadline)
    assert spy.count == 0
    with s.tx() as db:
        assert get(db, 'opus-capture:one') is None


# --------------------------------------------- Defect 2: budget rounding over-cap => 0 sends
def test_float_rounding_over_budget_zero_sends(tmp_path):
    s = Store(str(tmp_path / 'c.sqlite')); spy = SendSpy('fail')
    costs = envelope(); costs['model_input']['usd'] = '0.010001'  # 0.060001 > 0.06
    with pytest.raises(SendGateDenied, match='CAPTURE_COST_EXCEEDS_CAP'):
        send(s, spy, costs=costs)
    assert spy.count == 0
    with s.tx() as db:
        assert get(db, 'opus-capture:one') is None


def test_sub_micro_precision_zero_sends(tmp_path):
    s = Store(str(tmp_path / 'c.sqlite')); spy = SendSpy('fail')
    costs = envelope(); costs['telemetry']['usd'] = '0.0000001'
    with pytest.raises(SendGateDenied, match='COST_SUB_MICRO_PRECISION'):
        send(s, spy, costs=costs)
    assert spy.count == 0


# ------------------------------------------- identity / cost-basis pre-check => 0 sends
def test_identity_binding_failure_zero_sends(tmp_path):
    s = Store(str(tmp_path / 'c.sqlite')); spy = SendSpy('fail')
    with pytest.raises(SendGateDenied, match='CAPTURE_BINDING_REQUIRED'):
        send(s, spy, role='')
    assert spy.count == 0


def test_missing_cost_basis_zero_sends(tmp_path):
    s = Store(str(tmp_path / 'c.sqlite')); spy = SendSpy('fail')
    costs = envelope(); costs['gateway_policy']['basis'] = ''
    with pytest.raises(SendGateDenied, match='COST_BASIS_REQUIRED'):
        send(s, spy, costs=costs)
    assert spy.count == 0


# ----------------------------------------- concurrent preemption: 2 racers => exactly 1 send
def test_two_racing_callers_exactly_one_send(tmp_path):
    path = tmp_path / 'c.sqlite'
    Store(str(path))  # initialize schema
    total = {'n': 0}
    total_lock = threading.Lock()
    start = threading.Barrier(2)

    class SharedSpy:
        def post(self, endpoint, body, headers, timeout):
            with total_lock:
                total['n'] += 1
            raise RuntimeError('SignatureDoesNotMatch: IAM/Cedar not applied')

    def attempt(_):
        start.wait()
        try:
            guarded_capture_send(Store(str(path)), 'one', role='synthetic-role',
                workspace='synthetic-project', request_digest='a'*64,
                deadline=200, now=101, costs=envelope(), cap_usd='0.06',
                transport=SharedSpy(), endpoint=ENDPOINT,
                body={'model': 'x', 'max_tokens': 8}, headers={})
            return 'sent'
        except SendGateDenied as e:
            assert 'EXISTS' in str(e) or 'CONSUMED' in str(e)
            return 'denied'

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(attempt, range(2)))
    assert results == ['denied', 'sent']
    assert total['n'] == 1               # exactly ONE real send across both racers


# ------------------- crash-BEFORE-send (claim committed, died before dispatch) => 0 sends
def test_crash_before_send_restart_zero_sends(tmp_path):
    # Model a crash AFTER the atomic reserve+claim commits but BEFORE the real
    # dispatch fired: commit the claim directly (no send), then restart. The claim
    # is persisted (budget held) yet the actual send NEVER fired -> total sends 0,
    # and the restart cannot re-issue it (CAPTURE_TICKET_EXISTS) -> still 0 sends.
    from scripts.opus_capture_ticket import reserve_and_claim_capture
    path = tmp_path / 'c.sqlite'
    s = Store(str(path))
    reserve_and_claim_capture(s, 'one', role='synthetic-role', workspace='synthetic-project',
        request_digest='a'*64, deadline=200, now=100, costs=envelope(), cap_usd='0.06')
    # ... process dies here, before any transport.post. Restart re-attempts:
    reopened = Store(str(path)); spy = SendSpy('fail')
    with pytest.raises(SendGateDenied, match='EXISTS'):
        send(reopened, spy)
    assert spy.count == 0                 # crash-before-send => ZERO sends total
    with reopened.tx() as db:
        assert get(db, 'opus-capture:one')['held_usd'] == '0.06'  # hold retained


# ---- send-timeout + restart: total may be 1 (pre-timeout send); restart delta == 0
def test_send_timeout_restart_delta_zero_hold_retained(tmp_path):
    # A real send may have ALREADY fired ONCE before the timeout. The invariant is
    # NOT total==0; it is: restart adds ZERO new sends, hold retained, no auto-retry.
    path = tmp_path / 'c.sqlite'
    s = Store(str(path)); spy = SendSpy('timeout')
    out = send(s, spy)                    # dispatch fires once, then times out
    assert spy.count == 1                 # pre-timeout send counts as 1 (allowed)
    assert out['error'] is not None and 'timeout' in out['error'].lower()
    with s.tx() as db:
        row = get(db, 'opus-capture:one')
        assert row['state'] == 'UNKNOWN'          # unknown outcome, no refund
        assert row['held_usd'] == '0.06'          # full reservation retained
    # Restart must add ZERO new sends (no auto-retry of the timed-out call).
    reopened = Store(str(path)); restart_spy = SendSpy('fail')
    with pytest.raises(SendGateDenied, match='EXISTS'):
        send(reopened, restart_spy)
    assert restart_spy.count == 0         # restart DELTA == 0 (total across run stays 1)
    with reopened.tx() as db:
        assert get(db, 'opus-capture:one')['held_usd'] == '0.06'
