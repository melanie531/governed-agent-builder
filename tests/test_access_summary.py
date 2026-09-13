"""Task 3 (哥哥 refinement 2): the AI Catalog access summary must report the
authenticated caller's REAL, DEDUPLICATED authorization — not the catalog total,
and never counting "requestable" as "granted". Granted, requestable and callable
are distinct categories; callability is judged separately from granted.

Fixture mode with the seeded demo personas (synthetic data only).
"""
from .conftest import login


def _summary(client, persona):
    login(client, persona)
    body = client.get('/api/catalog').json()
    return body['access_summary'], body['count'], body['items']


def test_access_summary_is_per_user_real_granted_count_not_catalog_total(client):
    summary, total, items = _summary(client, 'alex')
    # Independently recompute the caller's deduplicated granted set from the
    # per-item projections the same response returned.
    top = [i for i in items if i['kind'] != 'tool' and not i.get('parent_id')]
    granted_ids = {i['id'] for i in top if i.get('granted') is True}
    requestable_ids = {i['id'] for i in top if i.get('granted') is not True and i.get('requestable') is True}
    assert summary['granted'] == len(granted_ids)
    assert summary['requestable'] == len(requestable_ids)
    assert summary['available'] == total
    # The granted count is the caller's real authorization, NOT the catalog size.
    assert summary['granted'] < summary['available']
    assert summary['granted'] > 0
    # Requestable is never folded into granted.
    assert granted_ids.isdisjoint(requestable_ids)


def test_requestable_is_not_counted_as_granted_and_grant_increments_dedup(client):
    before, total_before, items = _summary(client, 'alex')
    # external-gemini is requestable (not granted) for alex by default.
    reqable = {i['id'] for i in items if i.get('requestable') is True and i.get('granted') is not True}
    assert 'external-gemini' in reqable
    assert before['requestable'] >= 1

    # Admin grants the requestable component to alex.
    login(client, 'admin')
    r = client.post('/api/admin/grants', json={'persona_id': 'alex', 'component_id': 'external-gemini', 'enabled': True})
    assert r.status_code == 200, r.text

    after, total_after, _ = _summary(client, 'alex')
    # Granting moves the component from requestable -> granted (dedup: +1/-1).
    assert after['granted'] == before['granted'] + 1
    assert after['requestable'] == before['requestable'] - 1
    # Catalog total is unchanged (granted count is not the catalog size).
    assert total_after == total_before == after['available']

    # Re-enabling the SAME grant must not double-count (dedup).
    login(client, 'admin')
    client.post('/api/admin/grants', json={'persona_id': 'alex', 'component_id': 'external-gemini', 'enabled': True})
    again, _, _ = _summary(client, 'alex')
    assert again['granted'] == after['granted']


def test_callable_is_judged_separately_from_granted(client):
    summary, _, items = _summary(client, 'alex')
    top = [i for i in items if i['kind'] != 'tool' and not i.get('parent_id')]
    granted_ids = {i['id'] for i in top if i.get('granted') is True}
    # "callable" = granted AND execution binding verified; fixture demo routes are
    # granted but NOT execution-verified, so callable is strictly a subset and
    # here is zero even though grants exist. This proves callability != granted.
    callable_ids = {i['id'] for i in top if i['id'] in granted_ids and
                    (i.get('execution_ready') is True or (i.get('execution_binding') or {}).get('status') == 'verified')}
    assert summary['callable'] == len(callable_ids)
    assert summary['callable'] <= summary['granted']
    assert summary['callable'] == 0 and summary['granted'] > 0


def test_access_summary_is_distinct_per_authenticated_caller(client):
    alex_summary, _, _ = _summary(client, 'alex')
    sam_summary, _, _ = _summary(client, 'sam')
    # Different callers see their own authorization; the summary is per-user, not
    # a shared/global number.
    assert isinstance(alex_summary['granted'], int) and isinstance(sam_summary['granted'], int)
    assert (alex_summary['granted'], alex_summary['requestable']) != (sam_summary['granted'], sam_summary['requestable']) \
        or alex_summary != sam_summary
