"""Synthetic offline tests for the cached discovery catalog integration.

All records are explicitly synthetic test fixtures (synthetic=True,
'synthetic-*' ids). No real model names, release dates or official lists are
asserted here: the official review feed is owner-verified separately and
pending official data must surface as 'pending', never fabricated.
"""
import copy
import json

import pytest

from backend.aws_adapter import IntegrationNotConfigured
from backend.discovery_catalog_source import DiscoveryCatalogSource, load_discovery_cache

TODAY = '2026-09-13'


def scope():
    return {'workspaces': ['research'], 'owner': 'Platform owner',
            'data_handling': 'Synthetic development data only'}


def cache_body(records=None, status='pending'):
    return {'schema_version': 1,
            'official_review_status': status,
            'source': {'name': 'Owner official model review feed',
                       'url': 'https://example.invalid/synthetic-review-feed'},
            'generated_at': '2026-09-13T00:00:00Z',
            'records': records if records is not None else [synthetic_record()]}


def synthetic_record(**overrides):
    record = {'record_id': 'synthetic-recent-model',
              'vendor': 'Anthropic',
              'model_name': 'Synthetic Test Model',
              'capabilities': ['synthetic-capability'],
              'release_date': '2026-08-01',
              'source_url': 'https://example.invalid/synthetic-official-post',
              'synthetic': True,
              'review': {'reviewed_by': 'synthetic-reviewer',
                         'reviewed_at': '2026-09-12T00:00:00Z',
                         'status': 'approved'}}
    record.update(overrides)
    return record


def write_cache(tmp_path, body):
    path = tmp_path / 'discovery-cache.json'
    path.write_text(json.dumps(body))
    return str(path)


def source(tmp_path, body=None):
    path = write_cache(tmp_path, cache_body() if body is None else body)
    return DiscoveryCatalogSource(source_id='synthetic-discovery', cache_path=path,
                                  scope=scope(), today='2026-09-13')


def test_missing_cache_fails_loud_with_specific_error(tmp_path):
    adapter = DiscoveryCatalogSource(source_id='synthetic-discovery',
                                     cache_path=str(tmp_path / 'absent.json'),
                                     scope=scope(), today=TODAY)
    with pytest.raises(IntegrationNotConfigured, match='discovery cache'):
        adapter.records()


def test_corrupt_cache_fails_loud_never_empty_success(tmp_path):
    path = tmp_path / 'discovery-cache.json'
    path.write_text('{not json')
    adapter = DiscoveryCatalogSource(source_id='synthetic-discovery', cache_path=str(path),
                                     scope=scope(), today=TODAY)
    with pytest.raises(IntegrationNotConfigured, match='discovery cache'):
        adapter.records()


def test_unexpected_cache_schema_fails_loud(tmp_path):
    body = cache_body()
    body['schema_version'] = 99
    adapter = source(tmp_path, body)
    with pytest.raises(IntegrationNotConfigured, match='schema'):
        adapter.records()


def test_reviewed_record_projected_with_source_and_review_metadata(tmp_path):
    rows = source(tmp_path).records()
    assert len(rows) == 1
    row = rows[0]
    assert row['id'] == 'discovery:synthetic-discovery:synthetic-recent-model'
    assert row['kind'] == 'model'
    assert row['name'] == 'Synthetic Test Model'
    assert row['provider'] == 'Anthropic'
    assert row['release_date'] == '2026-08-01'
    assert row['source_url'] == 'https://example.invalid/synthetic-official-post'
    assert row['review'] == {'reviewed_by': 'synthetic-reviewer',
                             'reviewed_at': '2026-09-12T00:00:00Z', 'status': 'approved'}
    assert row['official_data_status'] == 'pending'
    assert row['discovery_only'] is True
    assert row['approved'] is True and row['fixture'] is False
    assert row['discoverable_workspaces'] == ['research']
    assert row['version'] and row['source_revision'] == row['version']


def test_discovery_separates_availability_protocol_gateway_grant_execution(tmp_path):
    rows = source(tmp_path).records()
    row = rows[0]
    # Facts the cache does not carry stay 'unknown', never True/available.
    assert row['region_availability'] == 'unknown'
    assert row['runtime_protocol'] == 'unknown'
    assert row['gateway_enumeration'] == 'NotConnected'
    assert row['entitlement'] == 'unverified'
    assert row['execution_ready'] is False
    assert row['integration_ready'] is False
    assert row['execution_binding'] == {'status': 'unverified', 'last_checked': None}
    # Discovery must never auto-create exposure/grants: not requestable.
    assert row['requestable'] is False


def test_poisoned_cache_cannot_mark_row_executable_or_available(tmp_path):
    record = synthetic_record(execution_ready=True, integration_ready=True,
                              entitlement='granted', gateway_enumeration='connected',
                              region_availability='available', runtime_protocol='compatible',
                              requestable=True,
                              execution_binding={'status': 'verified', 'last_checked': 1})
    row = source(tmp_path, cache_body([record])).records()[0]
    assert row['execution_ready'] is False and row['integration_ready'] is False
    assert row['execution_binding'] == {'status': 'unverified', 'last_checked': None}
    assert row['entitlement'] == 'unverified'
    assert row['gateway_enumeration'] == 'NotConnected'
    assert row['requestable'] is False
    # Owner-reviewed factual strings pass through, but booleans never become ready.
    assert row['region_availability'] == 'available'
    assert row['runtime_protocol'] == 'compatible'


def test_unreviewed_and_out_of_window_records_excluded_with_reasons(tmp_path):
    records = [synthetic_record(),
               synthetic_record(record_id='synthetic-no-review', review=None),
               synthetic_record(record_id='synthetic-old',
                                release_date='2026-01-01'),
               synthetic_record(record_id='synthetic-no-date', release_date=None)]
    adapter = source(tmp_path, cache_body(records))
    rows = adapter.records()
    assert [r['record_id'] for r in rows] == ['synthetic-recent-model']
    reasons = {e['record_id']: e['reason'] for e in adapter.last_excluded}
    assert reasons['synthetic-no-review'] == 'unreviewed_record'
    assert reasons['synthetic-old'] == 'outside_six_month_window'
    assert reasons['synthetic-no-date'] == 'missing_release_date'


def test_empty_verified_feed_is_empty_catalog_not_fabricated(tmp_path):
    adapter = source(tmp_path, cache_body(records=[]))
    assert adapter.records() == []


def test_status_reports_pending_official_data_and_counts(tmp_path):
    adapter = source(tmp_path, cache_body(
        [synthetic_record(), synthetic_record(record_id='synthetic-old', release_date='2026-01-01')]))
    adapter.records()
    status = adapter.status()
    assert status['official_data_status'] == 'pending'
    assert status['discovered'] == 1 and status['excluded'] == 1
    assert status['connection_state'] == 'connected'


def test_load_discovery_cache_requires_https_feed_source(tmp_path):
    body = cache_body()
    body['source']['url'] = 'http://example.invalid/insecure'
    path = write_cache(tmp_path, body)
    with pytest.raises(IntegrationNotConfigured, match='https'):
        load_discovery_cache(path)
