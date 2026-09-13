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


# --- 哥哥 review of 789293c: Defect 1 (non-finite time bypass) regressions ---
# NaN comparisons are always False, so `now>=deadline` with a NaN silently reads
# as "not expired". inf/-inf break the deadline bounds. All must fail closed.

@pytest.mark.parametrize('bad_now', [float('inf'), float('-inf'), float('nan')])
def test_reserve_rejects_non_finite_now(tmp_path, bad_now):
    s = Store(str(tmp_path / 'capture.sqlite'))
    with pytest.raises(ValueError, match='TIME_VALUE_NOT_FINITE'):
        reserve_capture(s, 'one', role='synthetic-role', workspace='synthetic-project',
            request_digest='a'*64, deadline=200, now=bad_now, costs=envelope(), cap_usd='0.06')


@pytest.mark.parametrize('bad_deadline', [float('inf'), float('-inf'), float('nan')])
def test_reserve_rejects_non_finite_deadline(tmp_path, bad_deadline):
    s = Store(str(tmp_path / 'capture.sqlite'))
    with pytest.raises(ValueError, match='TIME_VALUE_NOT_FINITE'):
        reserve_capture(s, 'one', role='synthetic-role', workspace='synthetic-project',
            request_digest='a'*64, deadline=bad_deadline, now=100, costs=envelope(), cap_usd='0.06')


@pytest.mark.parametrize('bad_now', [float('inf'), float('-inf'), float('nan')])
def test_consume_non_finite_now_never_bypasses_expiry(tmp_path, bad_now):
    # Regression: pre-fix, now=NaN passed `now>=deadline` (NaN cmp is False) and
    # CONSUMED the ticket -> a send. Non-finite now must be rejected, never allow.
    s = Store(str(tmp_path / 'capture.sqlite')); reserve(s)
    with pytest.raises(ValueError, match='TIME_VALUE_NOT_FINITE'):
        consume_capture(s, 'one', role='synthetic-role', workspace='synthetic-project',
            request_digest='a'*64, now=bad_now)
    # Ticket must remain RESERVED (unconsumed) after the fail-closed rejection.
    with s.tx() as db:
        from backend.foundation_runs import get
        assert get(db, 'opus-capture:one')['state'] == 'RESERVED'


def test_consume_non_finite_stored_deadline_treated_as_expired(tmp_path):
    # A corrupted non-finite stored deadline must fail closed (never "not expired").
    s = Store(str(tmp_path / 'capture.sqlite')); reserve(s)
    from backend.foundation_runs import get, put
    with s.tx() as db:
        row = get(db, 'opus-capture:one'); row['deadline'] = float('nan')
        put(db, 'opus-capture:one', row)
    with pytest.raises(ValueError, match='TIME_VALUE_NOT_FINITE'):
        consume_capture(s, 'one', role='synthetic-role', workspace='synthetic-project',
            request_digest='a'*64, now=101)


# --- 哥哥 review of 789293c: Defect 2 (float-rounding over-budget) regressions ---

def test_float_rounding_over_budget_is_rejected(tmp_path):
    # Six items summing to 0.060000000000000001 (exact) truly EXCEED cap 0.06.
    # A float accountant would round this to 0.06 and wrongly ADMIT it. Exact
    # integer micro-USD accounting must REJECT it.
    s = Store(str(tmp_path / 'capture.sqlite'))
    costs = envelope()
    costs['model_input']['usd'] = '0.010001'  # 0.010001 + 5*0.01 = 0.060001 > 0.06
    with pytest.raises(ValueError, match='CAPTURE_COST_EXCEEDS_CAP'):
        reserve(s, costs=costs)


def test_sub_micro_precision_cost_is_rejected(tmp_path):
    # Dust below one micro-USD cannot be represented as exact integer minor units
    # and must be rejected rather than silently truncated toward the cap.
    s = Store(str(tmp_path / 'capture.sqlite'))
    costs = envelope()
    costs['telemetry']['usd'] = '0.0000001'  # 0.1 micro-USD -> sub-micro precision
    with pytest.raises(ValueError, match='COST_SUB_MICRO_PRECISION'):
        reserve(s, costs=costs)


def test_boundary_exactly_at_cap_is_admitted(tmp_path):
    # Boundary is DEFINED: total == cap is admitted (strict > rejects only OVER).
    # Six items of 0.01 == 0.06 == cap.
    s = Store(str(tmp_path / 'capture.sqlite'))
    row = reserve(s, costs=envelope())  # exactly 0.06 == cap 0.06
    assert row['state'] == 'RESERVED'
    assert row['held_usd'] == '0.06'
    assert row['held_micros'] == 60000
