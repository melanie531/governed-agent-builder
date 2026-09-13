from decimal import Decimal, localcontext
from types import SimpleNamespace
import pytest
from scripts.opus_capture_ticket import _money,reserve_capture,dispatch_capture
from tests.test_capture_dispatch import prepared,REQUEST,admit
from tests.test_capture_ticket import envelope
from backend.store import Store
from backend.foundation_runs import get,put
from foundation_harness.config import digest
from foundation_harness.transport import IAMTransport
from botocore.credentials import Credentials


def test_exponent_ledger_is_exact(tmp_path):
    assert _money('1E-8')==Decimal('0.00000001')
    assert _money('10E-9')==_money('0.0000000100')
    with pytest.raises(ValueError):_money('1E-19')
    with localcontext() as ctx:
        ctx.prec=2
        assert _money('1.23456789E-8')==Decimal('0.0000000123456789')
    s=Store(str(tmp_path/'db'))
    with s.tx() as db:
        put(db,'capture-approval',{'role':'r','workspace':'w','digest':digest(REQUEST)})
        put(db,'foundation-budget:account',{'held_usd':'1E-8','estimated_usd':'0'})
    reserve_capture(s,'one',role='r',workspace='w',request_digest=digest(REQUEST),deadline=200,now=100,costs=envelope(),cap_usd='0.06',admission_check=admit,budget_scopes=('account','workspace:w'))
    with s.tx() as db:assert Decimal(get(db,'foundation-budget:account')['held_usd'])==Decimal('0.06000001')


def test_false_admission_cannot_dispatch(tmp_path):
    s=prepared(tmp_path)
    class Wire:
        def post(self,*args,**kwargs):raise AssertionError('must not send')
    with pytest.raises(ValueError,match='ADMISSION'):
        dispatch_capture(s,'one',role='r',workspace='w',request=REQUEST,transport=Wire(),admission_check=lambda *a:False,clock=lambda:101)


def test_false_reservation_creates_no_ticket_or_budget(tmp_path):
    s=Store(str(tmp_path/'db'))
    with pytest.raises(ValueError,match='ADMISSION'):
        reserve_capture(s,'one',role='r',workspace='w',request_digest=digest(REQUEST),deadline=200,now=100,costs=envelope(),cap_usd='0.06',admission_check=lambda *a:False,budget_scopes=('account','workspace:w'))
    with s.tx() as db:
        assert get(db,'opus-capture:one') is None
        assert get(db,'foundation-budget:account') is None
        assert get(db,'foundation-budget:workspace:w') is None


def test_trusted_identity_controls_budget_and_cannot_change(tmp_path):
    s=prepared(tmp_path)
    with s.tx() as db:
        assert get(db,'foundation-budget:user:trusted-user')['held_usd']=='0.06'
        assert get(db,'foundation-budget:agent:trusted-agent')['held_usd']=='0.06'
        assert get(db,'foundation-budget:user:r') is None
    class Wire:
        def post(self,*args):raise AssertionError('must not send')
    with pytest.raises(ValueError,match='IDENTITY_CHANGED'):
        dispatch_capture(s,'one',role='r',workspace='w',request=REQUEST,transport=Wire(),admission_check=lambda *a:{'allowed':True,'user_id':'other','agent_id':'trusted-agent'},clock=lambda:101)


def test_signing_crossing_deadline_prevents_send(tmp_path,monkeypatch):
    from foundation_harness import transport
    s=prepared(tmp_path);now=[101];calls=[]
    original=transport.SigV4Auth.add_auth
    def delayed(self,request):
        original(self,request);now[0]=201
    monkeypatch.setattr(transport.SigV4Auth,'add_auth',delayed)
    class Wire(IAMTransport):
        async def _send(self,*args):calls.append(args);return {},{}
    wire=Wire(SimpleNamespace(get_credentials=lambda:Credentials('SYNTHETIC','synthetic-secret')))
    with pytest.raises(TimeoutError):
        dispatch_capture(s,'one',role='r',workspace='w',request=REQUEST,transport=wire,admission_check=admit,clock=lambda:now[0])
    assert calls==[]
    with s.tx() as db:assert get(db,'foundation-budget:account')['held_usd']=='0.06'


@pytest.mark.parametrize('delay,expected',[ (100,False),(3,True)])
def test_credentials_delay_deducted_before_wire(tmp_path,delay,expected):
    s=prepared(tmp_path);now=[101];calls=[]
    class Session:
        def get_credentials(self):
            now[0]+=delay
            return Credentials('SYNTHETIC','synthetic-secret')
    class Wire(IAMTransport):
        async def _send(self,url,data,headers,timeout):
            calls.append(timeout);return {},{}
    if expected:
        dispatch_capture(s,'one',role='r',workspace='w',request=REQUEST,transport=Wire(Session()),admission_check=admit,clock=lambda:now[0])
        assert calls==[57]
    else:
        with pytest.raises((TimeoutError,ValueError)):
            dispatch_capture(s,'one',role='r',workspace='w',request=REQUEST,transport=Wire(Session()),admission_check=admit,clock=lambda:now[0])
        assert calls==[]
        with s.tx() as db:assert get(db,'opus-capture:one')['state']=='UNKNOWN'
