"""Bounded one-call Opus response-identity forensics tests. No live inference."""
from decimal import Decimal

import pytest

from foundation_harness.opus_forensics import (
    OpusResponseForensics, ForensicsMisuse, ONE_CALL,
)
from foundation_harness.opus_messages import REQUEST_MODEL

ENDPOINT = ('https://gab-foundation-model-m0-example.gateway.bedrock-agentcore'
            '.us-west-2.amazonaws.com/bedrockrt/v1/messages')
ROLE = 'arn:aws:iam::302277511711:role/synthetic-foundation-exec'


def good_response(model='opus5-observed-response-id'):
    return {'type': 'message', 'model': model, 'stop_reason': 'end_turn',
            'usage': {'input_tokens': 5, 'output_tokens': 3},
            'content': [{'type': 'text', 'text': 'ok'}]}


class FakeTransport:
    def __init__(self, response=None, raise_exc=None):
        self.response, self.raise_exc, self.calls = response, raise_exc, []

    def post(self, endpoint, body, headers, timeout):
        self.calls.append((endpoint, body, headers))
        if self.raise_exc:
            raise self.raise_exc
        return self.response, {'http_status': 200}


def make(transport, **kw):
    kw.setdefault('execution_role_arn', ROLE)
    kw.setdefault('reservation_usd', Decimal('0.50'))
    return OpusResponseForensics(transport, ENDPOINT, **kw)


def test_requires_execution_role_identity():
    with pytest.raises(ForensicsMisuse, match='EXECUTION_ROLE_IDENTITY_REQUIRED'):
        make(FakeTransport(), execution_role_arn='')


def test_requires_explicit_usd_budget_cap_decimal():
    with pytest.raises(ForensicsMisuse, match='USD_BUDGET_CAP_MUST_BE_DECIMAL'):
        make(FakeTransport(), reservation_usd=0.5)


@pytest.mark.parametrize('usd', [Decimal('0'), Decimal('-1'), Decimal('5.01')])
def test_usd_budget_cap_bounded(usd):
    with pytest.raises(ForensicsMisuse, match='USD_BUDGET_CAP_OUT_OF_RANGE'):
        make(FakeTransport(), reservation_usd=usd)


@pytest.mark.parametrize('tok', [0, 257, 512])
def test_output_token_cap_bounded(tok):
    with pytest.raises(ForensicsMisuse, match='OUTPUT_TOKEN_CAP_OUT_OF_RANGE'):
        make(FakeTransport(), max_output_tokens=tok)


def test_single_call_captures_response_model_and_records_identity():
    t = FakeTransport(good_response('opus5-observed-response-id'))
    receipt = make(t).capture('sys', 'synthetic prompt', {})
    assert len(t.calls) == ONE_CALL == 1
    body = t.calls[0][1]
    assert body['model'] == REQUEST_MODEL and body['stream'] is False
    assert body['thinking'] == {'type': 'disabled'} and body['max_tokens'] <= 256
    assert 'tools' not in body and 'tool_choice' not in body
    assert receipt['captured_response_model'] == 'opus5-observed-response-id'
    assert receipt['execution_role_arn'] == ROLE
    assert receipt['one_call_limit'] == 1 and receipt['model_calls_made'] == 1
    assert receipt['tool_calls'] == 0
    assert receipt['usd_budget_cap'] == '0.50'
    assert receipt['live_call_status'] == 'OBSERVED-live-response-model'


def test_never_sets_ready_flag():
    receipt = make(FakeTransport(good_response())).capture('sys', 'p', {})
    assert receipt['sets_ready_flag'] is False
    assert 'execution_ready' not in receipt and 'integration_ready' not in receipt
    assert 'ready' not in receipt


def test_one_call_limit_is_hard_no_second_attempt():
    f = make(FakeTransport(good_response()))
    f.capture('sys', 'p', {})
    with pytest.raises(ForensicsMisuse, match='ONE_CALL_ALREADY_SPENT'):
        f.capture('sys', 'p', {})


def test_transport_failure_marks_unverified_pending_apply_and_spends_the_call():
    t = FakeTransport(raise_exc=RuntimeError('SignatureDoesNotMatch: IAM not applied'))
    receipt = make(t).capture('sys', 'p', {})
    assert receipt['live_call_status'] == 'UNVERIFIED-pending-哥哥-apply'
    assert receipt['captured_response_model'] is None
    assert receipt['model_calls_made'] == 1  # unknown outcome still consumes the one call
    assert 'SignatureDoesNotMatch' in receipt['error']


def test_missing_response_model_identity_is_unverified():
    bad = good_response(); del bad['model']
    receipt = make(FakeTransport(bad)).capture('sys', 'p', {})
    assert receipt['live_call_status'] == 'UNVERIFIED-pending-哥哥-apply'
    assert receipt['error'] == 'NO_RESPONSE_MODEL_IDENTITY'
