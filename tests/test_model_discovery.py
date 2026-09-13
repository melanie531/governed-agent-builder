"""TDD tests for backend/model_discovery.py (new module, not wired to /api/catalog).

All model records here are EXPLICITLY SYNTHETIC test fixtures (synthetic=True,
names prefixed 'synthetic-'). No official model names or release dates are
asserted or fabricated; production data is injected by the owner later.
"""
from datetime import date

import pytest

from backend import model_discovery as md


def rec(record_id='synthetic-a', vendor='Anthropic', name='synthetic-model-a',
        release_date='2026-06-01', source_url='https://example.com/official-review/a',
        capabilities=('text',), synthetic=True, **extra):
    out = {'record_id': record_id, 'vendor': vendor, 'model_name': name,
           'release_date': release_date, 'source_url': source_url,
           'capabilities': list(capabilities), 'synthetic': synthetic}
    out.update(extra)
    return out


TODAY = date(2026, 9, 13)


class TestRollingWindow:
    def test_release_inside_window_is_discovered(self):
        result = md.discover([rec(release_date='2026-06-01')], today=TODAY)
        assert [r['record_id'] for r in result['discovered']] == ['synthetic-a']
        assert result['excluded'] == []

    def test_window_is_six_calendar_months_not_180_days(self):
        # 180 days before 2026-09-13 is 2026-03-17; six calendar months is 2026-03-13.
        inside = rec('synthetic-cal', release_date='2026-03-15')
        result = md.discover([inside], today=TODAY)
        assert [r['record_id'] for r in result['discovered']] == ['synthetic-cal']

    def test_release_before_window_start_is_excluded_with_reason(self):
        result = md.discover([rec(release_date='2026-03-12')], today=TODAY)
        assert result['discovered'] == []
        assert result['excluded'][0]['record_id'] == 'synthetic-a'
        assert result['excluded'][0]['reason'] == 'outside_six_month_window'

    def test_window_start_boundary_is_inclusive(self):
        result = md.discover([rec(release_date='2026-03-13')], today=TODAY)
        assert len(result['discovered']) == 1

    def test_month_end_clamps_instead_of_overflowing(self):
        # 2026-08-31 minus six calendar months clamps to 2026-02-28 (non-leap).
        assert md.window_start(date(2026, 8, 31)) == date(2026, 2, 28)

    def test_today_is_injectable_and_defaults_to_utc_date(self):
        # Default path must not crash and must use a real UTC date.
        result = md.discover([], today=None)
        assert result == {'discovered': [], 'excluded': []}
        assert isinstance(md.utc_today(), date)


class TestFailLoudValidation:
    def test_missing_release_date_is_excluded_with_explicit_reason(self):
        record = rec()
        del record['release_date']
        result = md.discover([record], today=TODAY)
        assert result['discovered'] == []
        assert result['excluded'][0]['reason'] == 'missing_release_date'

    def test_future_release_date_is_excluded_with_explicit_reason(self):
        result = md.discover([rec(release_date='2026-09-14')], today=TODAY)
        assert result['discovered'] == []
        assert result['excluded'][0]['reason'] == 'future_release_date'

    def test_non_iso_release_date_is_excluded_not_guessed(self):
        result = md.discover([rec(release_date='June 2026')], today=TODAY)
        assert result['discovered'] == []
        assert result['excluded'][0]['reason'] == 'invalid_release_date'

    def test_missing_source_url_is_excluded_with_explicit_reason(self):
        record = rec()
        del record['source_url']
        result = md.discover([record], today=TODAY)
        assert result['discovered'] == []
        assert result['excluded'][0]['reason'] == 'missing_source_url'

    def test_non_https_source_url_is_excluded(self):
        result = md.discover([rec(source_url='http://example.com/x')], today=TODAY)
        assert result['discovered'] == []
        assert result['excluded'][0]['reason'] == 'missing_source_url'

    def test_unknown_vendor_is_excluded_with_explicit_reason(self):
        result = md.discover([rec(vendor='')], today=TODAY)
        assert result['discovered'] == []
        assert result['excluded'][0]['reason'] == 'unknown_vendor'

    def test_vendor_metadata_cannot_grant_execution(self):
        for vendor in ('Writer', 'xAI', 'Google'):
            result = md.discover([rec(vendor=vendor)], today=TODAY)
            assert result['discovered'][0]['provider_api'] == 'unconfigured'
            assert result['discovered'][0]['execution_ready'] is False

    def test_duplicate_record_ids_fail_loud(self):
        with pytest.raises(ValueError, match='[Dd]uplicate'):
            md.discover([rec('synthetic-dup'), rec('synthetic-dup', name='synthetic-model-b')],
                        today=TODAY)

    def test_missing_record_id_fails_loud(self):
        record = rec()
        del record['record_id']
        with pytest.raises(ValueError, match='record_id'):
            md.discover([record], today=TODAY)

    def test_unmarked_synthetic_records_are_rejected_in_this_repo_state(self):
        # No verified official data exists yet: records without an explicit
        # verified=True owner attestation must not be discovered silently.
        record = rec(synthetic=False)
        del record['synthetic']
        result = md.discover([record], today=TODAY)
        assert result['discovered'] == []
        assert result['excluded'][0]['reason'] == 'unverified_record'


class TestDiscoveryOutputContract:
    """Discovery is not connection, authorization or execution readiness."""

    def test_discovered_record_is_never_execution_ready(self):
        result = md.discover([rec()], today=TODAY)
        item = result['discovered'][0]
        assert item['execution_ready'] is False
        assert item['integration_ready'] is False
        assert item['execution_binding'] == {'status': 'unverified', 'last_checked': None}
        assert item['discovery_only'] is True

    def test_discovered_record_does_not_infer_route_from_vendor(self):
        for vendor in ('Anthropic', 'Google'):
            row = md.discover([rec(vendor=vendor)], today=TODAY)['discovered'][0]
            assert row['provider_api'] == 'unconfigured'
            assert row['supported_endpoints'] == []
            assert row['external'] is False

    def test_discovered_record_preserves_official_metadata_verbatim(self):
        item = md.discover([rec()], today=TODAY)['discovered'][0]
        assert item['vendor'] == 'Anthropic'
        assert item['model_name'] == 'synthetic-model-a'
        assert item['release_date'] == '2026-06-01'
        assert item['source_url'] == 'https://example.com/official-review/a'
        assert item['capabilities'] == ['text']
        assert item['origin'] == 'Official model review records'

    def test_discover_does_not_mutate_input_records(self):
        record = rec()
        snapshot = dict(record, capabilities=list(record['capabilities']))
        md.discover([record], today=TODAY)
        assert record == snapshot

    def test_empty_verified_feed_yields_empty_catalog_not_fabricated_entries(self):
        # No official data yet => empty discovery list, never invented models.
        result = md.discover([], today=TODAY)
        assert result == {'discovered': [], 'excluded': []}


class TestHistoricalBindingLookup:
    """Old draft/binding lookups are separate from the new discovery list and
    are never deleted by window filtering."""

    def test_bound_model_outside_window_is_still_resolvable(self):
        old = rec('synthetic-old', release_date='2025-01-15')
        lookup = md.binding_lookup([old], today=TODAY)
        resolved = lookup.resolve('synthetic-old')
        assert resolved['record_id'] == 'synthetic-old'
        assert resolved['binding_retained'] is True
        assert resolved['in_discovery_window'] is False

    def test_bound_model_inside_window_is_flagged_in_window(self):
        lookup = md.binding_lookup([rec('synthetic-new', release_date='2026-08-01')], today=TODAY)
        assert lookup.resolve('synthetic-new')['in_discovery_window'] is True

    def test_unknown_binding_reference_is_explicit_unverified_not_dropped(self):
        lookup = md.binding_lookup([], today=TODAY)
        resolved = lookup.resolve('synthetic-ghost')
        assert resolved['record_id'] == 'synthetic-ghost'
        assert resolved['binding_retained'] is True
        assert resolved['status'] == 'unverified_reference'

    def test_window_filtering_never_removes_lookup_entries(self):
        old = rec('synthetic-old', release_date='2025-01-15')
        discovery = md.discover([old], today=TODAY)
        assert discovery['discovered'] == []  # filtered from the NEW list
        lookup = md.binding_lookup([old], today=TODAY)
        assert lookup.resolve('synthetic-old')['binding_retained'] is True

    def test_lookup_resolution_is_never_execution_ready(self):
        lookup = md.binding_lookup([rec('synthetic-old', release_date='2025-01-15')], today=TODAY)
        resolved = lookup.resolve('synthetic-old')
        assert resolved['execution_ready'] is False
        assert resolved['execution_binding']['status'] == 'unverified'

    def test_poisoned_input_readiness_flags_cannot_leak_through(self):
        poisoned = rec('synthetic-poison', execution_ready=True, integration_ready=True,
                       execution_binding={'status': 'verified'}, discovery_only=False)
        item = md.discover([poisoned], today=TODAY)['discovered'][0]
        assert item['execution_ready'] is False
        assert item['integration_ready'] is False
        assert item['execution_binding'] == {'status': 'unverified', 'last_checked': None}
        assert item['discovery_only'] is True
        lookup = md.binding_lookup([poisoned], today=TODAY)
        resolved = lookup.resolve('synthetic-poison')
        assert resolved['execution_ready'] is False
        assert resolved['execution_binding'] == {'status': 'unverified', 'last_checked': None}
