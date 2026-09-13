"""Independent capture ticket tests: SQLite persistence, no cloud inference."""
import pytest
from scripts.opus_capture_ticket import reserve_capture, consume_capture, finish_capture
from backend.store import Store


def envelope():
    # Synthetic test dollars, NOT approved production pricing.
    names=['model_input','model_output','gateway_policy','runtime_lifetime','storage','telemetry']
    return {k:{'usd':'0.01','basis':'synthetic-test-only'} for k in names}


def reserve(store, ticket='one', costs=None):
    return reserve_capture(store,ticket,role='synthetic-role',workspace='synthetic-project',
        request_digest='a'*64,deadline=200,now=100,costs=envelope() if costs is None else costs,
        cap_usd='0.06')


def test_reopen_replay_timeout_never_refunds_or_dispatches_twice(tmp_path):
    path=tmp_path/'capture.sqlite';s=Store(str(path));reserve(s)
    assert consume_capture(s,'one',role='synthetic-role',workspace='synthetic-project',request_digest='a'*64,now=101)['state']=='CLAIMED'
    reopened=Store(str(path))
    with pytest.raises(ValueError,match='CONSUMED'):
        consume_capture(reopened,'one',role='synthetic-role',workspace='synthetic-project',request_digest='a'*64,now=102)
    finish_capture(reopened,'one',outcome='UNKNOWN')
    with pytest.raises(ValueError,match='CONSUMED'):
        consume_capture(reopened,'one',role='synthetic-role',workspace='synthetic-project',request_digest='a'*64,now=103)
    with reopened.tx() as db:
        from backend.foundation_runs import get
        assert get(db,'opus-capture:one')['held_usd']=='0.06'
        assert get(db,'opus-capture:one')['state']=='UNKNOWN'
    with pytest.raises(ValueError,match='EXISTS'):reserve(reopened)


def test_concurrent_claim_has_exactly_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path=tmp_path/'capture.sqlite';s=Store(str(path));reserve(s)
    def claim(_):
        try:
            consume_capture(Store(str(path)),'one',role='synthetic-role',workspace='synthetic-project',request_digest='a'*64,now=101)
            return 'claimed'
        except ValueError as e:
            assert 'CONSUMED' in str(e)
            return 'denied'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(claim,range(2)))
    assert sorted(results)==['claimed','denied']


@pytest.mark.parametrize('change',[{'role':'other'},{'workspace':'other'},{'request_digest':'b'*64},{'now':201}])
def test_bound_identity_and_expiry_reject_before_consumption(tmp_path,change):
    s=Store(str(tmp_path/'capture.sqlite'));reserve(s)
    args=dict(role='synthetic-role',workspace='synthetic-project',request_digest='a'*64,now=101);args.update(change)
    with pytest.raises(ValueError):consume_capture(s,'one',**args)


@pytest.mark.parametrize('invalid',['missing','unknown','overcap','no_basis'])
def test_budget_requires_all_services_and_actual_basis(tmp_path,invalid):
    s=Store(str(tmp_path/'capture.sqlite'));costs=envelope()
    if invalid=='missing':del costs['runtime_lifetime']
    elif invalid=='unknown':costs['telemetry']['usd']=None
    elif invalid=='overcap':costs['model_input']['usd']='1'
    else:costs['gateway_policy']['basis']=''
    with pytest.raises(ValueError):reserve(s,costs=costs)
