"""Capture dispatch uses actual IAMTransport with stubbed wire, not live AWS."""
import pytest
from backend.store import Store
from backend.foundation_runs import get,put
from scripts.opus_capture_ticket import reserve_capture,dispatch_capture
from foundation_harness.config import digest
from tests.test_capture_ticket import envelope
URL='https://gab-foundation-model-m0-example.gateway.bedrock-agentcore.us-west-2.amazonaws.com/bedrockrt/v1/messages'
REQUEST={'endpoint':URL,'system':'Short','prompt':'Hello','max_tokens':256}


def admit(db,role,workspace,request_digest):
    if get(db,'capture-approval')!={'role':role,'workspace':workspace,'digest':request_digest}:
        raise ValueError('NO_CAPTURE_ADMISSION')


def prepared(tmp_path):
    s=Store(str(tmp_path/'db'))
    with s.tx() as db:put(db,'capture-approval',{'role':'r','workspace':'w','digest':digest(REQUEST)})
    reserve_capture(s,'one',role='r',workspace='w',request_digest=digest(REQUEST),deadline=200,now=100,costs=envelope(),cap_usd='0.06',admission_check=admit,budget_scopes=('account','workspace:w'))
    return s


def test_timeout_consumes_once_and_holds_aggregate_budget(tmp_path):
    s=prepared(tmp_path);calls=[]
    class Wire:
        requires_reservation=True
        def post(self,*args):
            with s.tx() as db:
                assert get(db,'opus-capture:one')['state']=='CLAIMED'
                assert get(db,'foundation-budget:account')['held_usd']=='0.06'
            calls.append(args);raise TimeoutError('unknown')
    with pytest.raises(TimeoutError):
        dispatch_capture(s,'one',role='r',workspace='w',request=REQUEST,transport=Wire(),admission_check=admit,clock=lambda:101)
    assert len(calls)==1  # A send already occurred before the timeout.
    reopened=Store(str(tmp_path/'db'));before_retry=len(calls)
    with pytest.raises(ValueError,match='CONSUMED'):
        dispatch_capture(reopened,'one',role='r',workspace='w',request=REQUEST,transport=Wire(),admission_check=admit,clock=lambda:102)
    assert len(calls)-before_retry==0  # No new sends after persistence reopen.
    with s.tx() as db:
        assert get(db,'opus-capture:one')['state']=='UNKNOWN'
        assert get(db,'foundation-budget:account')['held_usd']=='0.06'


def test_actual_iam_transport_signs_once_after_durable_claim(tmp_path):
    from foundation_harness.transport import IAMTransport
    from botocore.credentials import Credentials
    from types import SimpleNamespace
    import json
    s=prepared(tmp_path);sends=[]
    class Wire(IAMTransport):
        async def _send(self,url,data,headers,timeout):
            with s.tx() as db:assert get(db,'opus-capture:one')['state']=='CLAIMED'
            authorization=next(v for k,v in headers.items() if k.lower()=='authorization')
            assert '/bedrock-agentcore/aws4_request' in authorization
            assert json.loads(data)['thinking']=={'type':'disabled'}
            sends.append(url)
            return {'model':'unverified-capture-only'},{}
    wire=Wire(SimpleNamespace(get_credentials=lambda:Credentials('SYNTHETIC','synthetic-secret')))
    result=dispatch_capture(s,'one',role='r',workspace='w',request=REQUEST,transport=wire,admission_check=admit,clock=lambda:101)
    assert result[0]['model']=='unverified-capture-only'
    with pytest.raises(ValueError):dispatch_capture(s,'one',role='r',workspace='w',request=REQUEST,transport=wire,admission_check=admit,clock=lambda:102)
    assert sends==[URL]


def test_revoke_after_reservation_prevents_send(tmp_path):
    s=prepared(tmp_path)
    with s.tx() as db:put(db,'capture-approval',{})
    class Wire:
        def post(self,*args):raise AssertionError('must not dispatch')
    with pytest.raises(ValueError,match='ADMISSION'):
        dispatch_capture(s,'one',role='r',workspace='w',request=REQUEST,transport=Wire(),admission_check=admit,clock=lambda:101)


def test_claimed_then_crashed_before_send_has_zero_sends_after_reopen(tmp_path):
    from scripts.opus_capture_ticket import consume_capture
    s=prepared(tmp_path)
    consume_capture(s,'one',role='r',workspace='w',request_digest=digest(REQUEST),now=101,admission_check=admit)
    calls=[]
    class Wire:
        def post(self,*args):calls.append(args)
    reopened=Store(str(tmp_path/'db'))
    with pytest.raises(ValueError,match='CONSUMED'):
        dispatch_capture(reopened,'one',role='r',workspace='w',request=REQUEST,transport=Wire(),admission_check=admit,clock=lambda:102)
    assert calls==[]
    with reopened.tx() as db:assert get(db,'foundation-budget:account')['held_usd']=='0.06'


def test_overall_cap_rolls_back_ticket_and_all_buckets(tmp_path):
    s=Store(str(tmp_path/'db'))
    with s.tx() as db:
        put(db,'capture-approval',{'role':'r','workspace':'w','digest':digest(REQUEST)})
        put(db,'foundation-budget:account',{'held_usd':'4.99','estimated_usd':'0'})
    with pytest.raises(ValueError,match='TOTAL_BUDGET'):
        reserve_capture(s,'one',role='r',workspace='w',request_digest=digest(REQUEST),deadline=200,now=100,costs=envelope(),cap_usd='0.06',admission_check=admit,budget_scopes=('workspace:w','account'))
    with s.tx() as db:
        assert get(db,'opus-capture:one') is None
        assert get(db,'foundation-budget:workspace:w') is None
        assert get(db,'foundation-budget:account')['held_usd']=='4.99'
