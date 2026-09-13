"""Send-path gate: atomic ticket claim + budget reservation BEFORE dispatch.

Proves the real send entry consumes the ticket and reserves budget atomically so
a call sends AT MOST ONCE, and that two racing callers yield exactly one dispatch.
No live inference; the live call stays UNVERIFIED-pending-哥哥-IAM/Cedar-apply.
"""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from scripts.opus_capture_ticket import reserve_capture
from scripts.opus_capture_send import guarded_capture_send, SendGateDenied
from backend.store import Store


def envelope():
    names = ['model_input', 'model_output', 'gateway_policy', 'runtime_lifetime', 'storage', 'telemetry']
    return {k: {'usd': '0.01', 'basis': 'synthetic-test-only'} for k in names}


def reserve(store, ticket='one'):
    return reserve_capture(store, ticket, role='synthetic-role', workspace='synthetic-project',
        request_digest='a'*64, deadline=200, now=100, costs=envelope(), cap_usd='0.06')


class CountingTransport:
    """Records every real dispatch; a live send would hit Bedrock here."""
    def __init__(self):
        self.posts = 0
        self.lock = threading.Lock()

    def post(self, endpoint, body, headers, timeout):
        with self.lock:
            self.posts += 1
        # No live call: IAM/Cedar not applied. Simulate the reviewed transport
        # being unable to reach a live provider (UNVERIFIED-pending-apply).
        raise RuntimeError('SignatureDoesNotMatch: IAM/Cedar not applied')


ENDPOINT = ('https://gab-foundation-model-m0-example.gateway.bedrock-agentcore'
            '.us-west-2.amazonaws.com/bedrockrt/v1/messages')


def test_send_consumes_ticket_and_reserves_budget_before_dispatch(tmp_path):
    s = Store(str(tmp_path / 'capture.sqlite')); reserve(s)
    t = CountingTransport()
    out = guarded_capture_send(s, 'one', role='synthetic-role', workspace='synthetic-project',
        request_digest='a'*64, now=101, transport=t, endpoint=ENDPOINT,
        body={'model': 'x', 'max_tokens': 8}, headers={})
    # Ticket was CLAIMED (budget reserved) and exactly one dispatch happened.
    assert t.posts == 1
    assert out['dispatched'] is True
    assert out['held_usd'] == '0.06' and out['held_micros'] == 60000
    assert out['live_call_status'] == 'UNVERIFIED-pending-哥哥-apply'
    assert out['sets_ready_flag'] is False
    # Ticket is consumed; state is UNKNOWN (unknown outcome retains full reserve).
    with s.tx() as db:
        from backend.foundation_runs import get
        row = get(db, 'opus-capture:one')
        assert row['state'] == 'UNKNOWN'
        assert row['held_usd'] == '0.06'  # no refund on unknown outcome


def test_second_send_after_claim_never_dispatches_again(tmp_path):
    s = Store(str(tmp_path / 'capture.sqlite')); reserve(s)
    t = CountingTransport()
    guarded_capture_send(s, 'one', role='synthetic-role', workspace='synthetic-project',
        request_digest='a'*64, now=101, transport=t, endpoint=ENDPOINT,
        body={'model': 'x', 'max_tokens': 8}, headers={})
    # A second attempt on the same ticket must be denied and MUST NOT dispatch.
    with pytest.raises(SendGateDenied, match='CONSUMED'):
        guarded_capture_send(s, 'one', role='synthetic-role', workspace='synthetic-project',
            request_digest='a'*64, now=102, transport=t, endpoint=ENDPOINT,
            body={'model': 'x', 'max_tokens': 8}, headers={})
    assert t.posts == 1  # still exactly one dispatch total


def test_two_racing_callers_only_one_sends(tmp_path):
    # TOCTOU proof: two callers race the same ticket; exactly one claims+dispatches.
    path = tmp_path / 'capture.sqlite'
    s = Store(str(path)); reserve(s)
    posts = {'n': 0}
    posts_lock = threading.Lock()
    start = threading.Barrier(2)

    class SharedCounter:
        def post(self, endpoint, body, headers, timeout):
            with posts_lock:
                posts['n'] += 1
            raise RuntimeError('SignatureDoesNotMatch: IAM/Cedar not applied')

    def attempt(_):
        start.wait()
        try:
            guarded_capture_send(Store(str(path)), 'one', role='synthetic-role',
                workspace='synthetic-project', request_digest='a'*64, now=101,
                transport=SharedCounter(), endpoint=ENDPOINT,
                body={'model': 'x', 'max_tokens': 8}, headers={})
            return 'sent'
        except SendGateDenied as e:
            assert 'CONSUMED' in str(e)
            return 'denied'

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(attempt, range(2)))
    assert results == ['denied', 'sent']
    assert posts['n'] == 1  # only ONE of two racing callers actually dispatched


def test_non_finite_now_gate_never_dispatches(tmp_path):
    # Defect 1 tie-in at the send path: non-finite now fails closed, no dispatch.
    s = Store(str(tmp_path / 'capture.sqlite')); reserve(s)
    t = CountingTransport()
    with pytest.raises(SendGateDenied, match='TIME_VALUE_NOT_FINITE'):
        guarded_capture_send(s, 'one', role='synthetic-role', workspace='synthetic-project',
            request_digest='a'*64, now=float('nan'), transport=t, endpoint=ENDPOINT,
            body={'model': 'x', 'max_tokens': 8}, headers={})
    assert t.posts == 0
    with s.tx() as db:
        from backend.foundation_runs import get
        assert get(db, 'opus-capture:one')['state'] == 'RESERVED'
