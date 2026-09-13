"""Synthetic offline tests: discovery cache wired through configured_catalog
into the real /api/catalog serving chain (TestClient), not module-only tests.

All records are explicitly synthetic (synthetic=True, 'synthetic-*' ids);
no real model names or official dates are asserted.
"""
import copy
import json
import os
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.live_catalog import configured_catalog
from .conftest import login, ORIGIN
from .test_discovery_catalog_integration import cache_body, synthetic_record, scope

ACCOUNT = '998877665544'


def write_cache(tmp_path, body=None):
    path = tmp_path / 'discovery-cache.json'
    path.write_text(json.dumps(cache_body() if body is None else body))
    return str(path)


def config(cache_path):
    return {'schema_version': 2, 'approved': True,
            'binding': {'expected_account': ACCOUNT, 'region': 'us-west-2',
                        'owner_approval': 'synthetic'},
            'discovery_sources': [{'approved': True, 'source_id': 'synthetic-discovery',
                                   'cache_path': cache_path, 'scope': scope(),
                                   'today': '2026-09-13'}]}


def session():
    s = Mock(region_name='us-west-2')
    s.client.return_value.get_caller_identity.return_value = {'Account': ACCOUNT}
    return s


def build(cache_path):
    return configured_catalog(config(cache_path), session=session(), client_factory=Mock())


def test_configured_catalog_accepts_explicit_discovery_source(tmp_path):
    catalog = build(write_cache(tmp_path))
    assert catalog is not None
    rows = catalog.records()
    assert [r['record_id'] for r in rows] == ['synthetic-recent-model']
    assert rows[0]['execution_ready'] is False
    status = catalog.source_status()
    assert status['Discovery']['connection_state'] == 'connected'
    assert status['Discovery']['official_data_status'] == 'pending'


def test_configured_catalog_without_discovery_key_unchanged(tmp_path):
    cfg = config(write_cache(tmp_path))
    cfg.pop('discovery_sources')
    assert configured_catalog(cfg, session=Mock(), client_factory=Mock()) is None


def test_missing_cache_fails_construction_with_specific_error(tmp_path):
    with pytest.raises(Exception, match='discovery cache'):
        build(str(tmp_path / 'absent.json'))


def test_unapproved_discovery_source_rejected(tmp_path):
    cfg = config(write_cache(tmp_path))
    cfg['discovery_sources'][0]['approved'] = False
    with pytest.raises(ValueError, match='[Aa]pproved discovery'):
        configured_catalog(cfg, session=session(), client_factory=Mock())


def live_client(tmp_path, monkeypatch, cache_path):
    monkeypatch.setenv('CATALOG_MODE', 'live')
    app = create_app(str(tmp_path / 'live.sqlite'), demo_mode=True, worker_enabled=False,
                     catalog_provider=build(cache_path))
    return TestClient(app, base_url=ORIGIN)


def test_api_catalog_serves_discovery_rows_with_review_metadata(tmp_path, monkeypatch):
    with live_client(tmp_path, monkeypatch, write_cache(tmp_path)) as c:
        login(c)
        catalog = c.get('/api/catalog').json()
        assert catalog['mode'] == 'live'
        items = {i['id']: i for i in catalog['items']}
        row = items['discovery:synthetic-discovery:synthetic-recent-model']
        assert row['kind'] == 'model' and row['provider'] == 'Anthropic'
        # Source/review provenance served through the real API:
        assert row['source_url'] == 'https://example.invalid/synthetic-official-post'
        assert row['release_date'] == '2026-08-01'
        assert row['review']['status'] == 'approved'
        assert row['official_data_status'] == 'pending'
        assert row['discovery_only'] is True
        # Discovery ≠ availability ≠ protocol ≠ gateway ≠ grant ≠ execution:
        assert row['region_availability'] == 'unknown'
        assert row['runtime_protocol'] == 'unknown'
        assert row['gateway_enumeration'] == 'NotConnected'
        assert row['entitlement'] == 'unverified'
        assert row['execution_ready'] is False
        assert row['execution_binding'] == {'status': 'unverified', 'last_checked': None}
        # No auto exposure/grant from discovery:
        assert row['granted'] is False and row['usable'] is False
        assert row['requestable'] is False
        assert catalog['sources']['Discovery']['official_data_status'] == 'pending'
        # Detail route serves the same discovery record:
        detail = c.get('/api/catalog/discovery:synthetic-discovery:synthetic-recent-model').json()
        assert detail['source_url'] == row['source_url'] and not detail['usable']


def test_api_catalog_hides_discovery_rows_from_other_workspaces(tmp_path, monkeypatch):
    with live_client(tmp_path, monkeypatch, write_cache(tmp_path)) as c:
        login(c, 'sam')
        catalog = c.get('/api/catalog').json()
        assert all(not i['id'].startswith('discovery:') for i in catalog['items'])


def test_api_catalog_fails_loud_when_cache_removed_at_runtime(tmp_path, monkeypatch):
    cache_path = write_cache(tmp_path)
    with live_client(tmp_path, monkeypatch, cache_path) as c:
        login(c)
        assert c.get('/api/catalog').status_code == 200
        os.remove(cache_path)
        response = c.get('/api/catalog')
        assert response.status_code == 503
        assert 'no fixture fallback' in response.json()['detail']


def test_discovery_rows_not_selectable_in_builder_and_no_grant_created(tmp_path, monkeypatch):
    with live_client(tmp_path, monkeypatch, write_cache(tmp_path)) as c:
        login(c)
        options = c.get('/api/build-options').json()
        assert options['catalog_mode'] == 'live'
        for foundation in options['foundations']:
            assert 'discovery:synthetic-discovery:synthetic-recent-model' not in foundation['models']
        login(c, 'admin')
        admin = c.get('/api/admin/catalog').json()
        # discovery never auto-creates exposure or grants:
        assert not [g for g in admin['grants']
                    if str(g.get('component', '')).startswith('discovery:')]


def test_historical_binding_outside_window_still_resolvable(tmp_path, monkeypatch):
    """Rolling-window discovery filtering must not delete historical bindings."""
    old = synthetic_record(record_id='synthetic-old-bound', release_date='2026-01-01')
    body = cache_body([synthetic_record(), old])
    cache_path = write_cache(tmp_path, body)
    with live_client(tmp_path, monkeypatch, cache_path) as c:
        login(c)
        items = [i['id'] for i in c.get('/api/catalog').json()['items']]
        assert 'discovery:synthetic-discovery:synthetic-old-bound' not in items
    from datetime import date

    from backend import model_discovery
    lookup = model_discovery.binding_lookup([synthetic_record(), old], today=date(2026, 9, 13))
    resolved = lookup.resolve('synthetic-old-bound')
    assert resolved['status'] == 'resolved' and resolved['binding_retained'] is True
    assert resolved['in_discovery_window'] is False
    assert resolved['execution_ready'] is False


def test_default_models_list_excludes_out_of_window_even_though_raw_feed_returns_them(tmp_path, monkeypatch):
    """Refinement 1: the DEFAULT /api/catalog Models list must return ONLY
    last-6-months models server-side, even though the raw owner-reviewed feed
    ALSO contains an older, out-of-window model. The recency window is a real
    backend filter (model_discovery.discover -> outside_six_month_window), not a
    frontend visual hide. AND a saved draft binding to the out-of-window model
    must be preserved (binding_retained), never broken."""
    from datetime import date

    from backend import model_discovery

    recent = synthetic_record()  # release_date 2026-08-01 -> in window @2026-09-13
    old = synthetic_record(record_id='synthetic-old-bound', release_date='2026-01-01')
    # The RAW feed carries BOTH the recent and the out-of-window model.
    body = cache_body([recent, old])
    cache_path = write_cache(tmp_path, body)
    with live_client(tmp_path, monkeypatch, cache_path) as c:
        login(c)
        payload = c.get('/api/catalog').json()
        ids = {i['id'] for i in payload['items']}
        model_ids = {i['id'] for i in payload['items'] if i['kind'] == 'model'}
        # DEFAULT list: recent present; out-of-window filtered out SERVER-SIDE.
        assert 'discovery:synthetic-discovery:synthetic-recent-model' in ids
        assert 'discovery:synthetic-discovery:synthetic-old-bound' not in ids
        # Every served discovery model is within the recent window.
        assert model_ids == {'discovery:synthetic-discovery:synthetic-recent-model'}

    # The exclusion reason is the real six-month window (server-side proof) at
    # the discovery source that feeds the live /api/catalog chain.
    from backend.discovery_catalog_source import DiscoveryCatalogSource
    src = DiscoveryCatalogSource(source_id='synthetic-discovery', cache_path=cache_path,
                                 scope=scope(), today='2026-09-13')
    served = [r['record_id'] for r in src.records()]
    assert served == ['synthetic-recent-model']
    reasons = {e['record_id']: e['reason'] for e in src.last_excluded}
    assert reasons.get('synthetic-old-bound') == 'outside_six_month_window'

    # Binding preservation for an existing draft pinned to the out-of-window model.
    lookup = model_discovery.binding_lookup([recent, old], today=date(2026, 9, 13))
    resolved = lookup.resolve('synthetic-old-bound')
    assert resolved['binding_retained'] is True
    assert resolved['in_discovery_window'] is False
