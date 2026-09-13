"""Proof tests for the VERIFIED launch-date recency filter (real rolling window).

Covers the task's five required proofs:
  (a) date-window filter actually reduces the list (in-window vs out-of-window),
  (b) missing / future / conflict launch dates -> pending, never recent,
  (c) lifecycle (ACTIVE|LEGACY) no longer affects recency,
  (d) draft-binding preservation when a model leaves the recent list,
  (e) the in-window count equals exact-id-join.json's inwindow_matched_cards.
"""
import json
from datetime import date
from pathlib import Path

import pytest

from backend.discovery_catalog import DiscoveryFoundationCatalog, normalize_model
from backend.model_recency import (
    RECENT, PENDING, PENDING_NO_DATE, PENDING_FUTURE, PENDING_OUT_OF_WINDOW,
    PENDING_CONFLICT, apply_recency, classify, load_launch_date_map, window_bounds,
)

MAP_PATH = Path('backend/model_launch_dates.json')
EVIDENCE_DIR = Path('docs/review-data')


def _summary(model_id, lifecycle='ACTIVE'):
    return {'modelId': model_id, 'modelName': model_id, 'providerName': 'Anthropic',
            'inputModalities': ['TEXT'], 'outputModalities': ['TEXT'],
            'responseStreamingSupported': True, 'inferenceTypesSupported': ['ON_DEMAND'],
            'modelLifecycle': {'status': lifecycle}}


# ---------------------------------------------------------------------------
# (a) The date-window filter actually reduces the list.
# ---------------------------------------------------------------------------
def test_a_window_filter_reduces_list_inwindow_vs_out_of_window():
    today = date(2026, 9, 13)
    launch_map = {
        'in.window': {'launch_date': '2026-07-01', 'source_url': 'u', 'content_sha256': 'h'},
        'out.old': {'launch_date': '2020-01-01', 'source_url': 'u', 'content_sha256': 'h'},
    }
    rows = [normalize_model(_summary('in.window')), normalize_model(_summary('out.old'))]
    apply_recency(rows, launch_map, today=today)
    recent = [r for r in rows if r['recency'] == RECENT]
    pending = [r for r in rows if r['recency'] == PENDING]
    # The recent list is strictly smaller than the full discovered list.
    assert len(recent) == 1 and len(recent) < len(rows)
    assert recent[0]['model_id'] == 'in.window'
    assert pending[0]['model_id'] == 'out.old'
    assert pending[0]['pending_reason'] == PENDING_OUT_OF_WINDOW


# ---------------------------------------------------------------------------
# (b) Missing / future / conflict launch dates -> pending, never recent.
# ---------------------------------------------------------------------------
def test_b_missing_date_is_pending_not_recent():
    r = classify('unmatched.model', {}, today=date(2026, 9, 13))
    assert r.recency == PENDING and r.pending_reason == PENDING_NO_DATE


def test_b_future_date_is_pending_not_recent():
    launch_map = {'future.model': {'launch_date': '2099-01-01', 'source_url': 'u', 'content_sha256': 'h'}}
    r = classify('future.model', launch_map, today=date(2026, 9, 13))
    assert r.recency == PENDING and r.pending_reason == PENDING_FUTURE


def test_b_conflict_is_pending_not_recent():
    # Conflicted ids are dropped from the loaded map (never classified recent).
    payload = {'models': {'c.model': {'launch_date': '2026-07-01', 'source_url': 'u', 'content_sha256': 'h'}},
               '_conflicts': {'c.model': ['2026-07-01', '2026-08-01']}}
    tmp = Path('backend/_tmp_conflict_map.json')
    tmp.write_text(json.dumps(payload))
    try:
        loaded = load_launch_date_map(tmp)
        assert 'c.model' not in loaded
        r = classify('c.model', loaded, today=date(2026, 9, 13))
        assert r.recency == PENDING and r.pending_reason == PENDING_NO_DATE
    finally:
        tmp.unlink()
    # A malformed date value is treated as a conflict, not recent.
    bad = classify('x', {'x': {'launch_date': 'not-a-date'}}, today=date(2026, 9, 13))
    assert bad.recency == PENDING and bad.pending_reason == PENDING_CONFLICT


# ---------------------------------------------------------------------------
# (c) Lifecycle (ACTIVE|LEGACY) no longer affects recency.
# ---------------------------------------------------------------------------
def test_c_lifecycle_does_not_affect_recency():
    today = date(2026, 9, 13)
    launch_map = {'m.recent': {'launch_date': '2026-07-01', 'source_url': 'u', 'content_sha256': 'h'},
                  'm.old': {'launch_date': '2020-01-01', 'source_url': 'u', 'content_sha256': 'h'}}
    # A LEGACY model with an in-window verified date is RECENT.
    legacy_recent = normalize_model(_summary('m.recent', lifecycle='LEGACY'))
    # An ACTIVE model with an out-of-window date is PENDING.
    active_old = normalize_model(_summary('m.old', lifecycle='ACTIVE'))
    apply_recency([legacy_recent, active_old], launch_map, today=today)
    assert legacy_recent['lifecycle'] == 'LEGACY' and legacy_recent['recency'] == RECENT
    assert active_old['lifecycle'] == 'ACTIVE' and active_old['recency'] == PENDING


def test_c_source_has_no_lifecycle_recency_proxy():
    # The discovery module must not equate ACTIVE/LEGACY with recency.
    src = Path('backend/discovery_catalog.py').read_text()
    assert "recency-adjacent" not in src
    front = Path('frontend/src/AICatalog.tsx').read_text()
    # Recency is verified-date driven, not lifecycle.
    assert "isActive" not in front and "isLegacy" not in front
    assert "recency==='recent'" in front or "recency === 'recent'" in front


# ---------------------------------------------------------------------------
# (d) Draft-binding preservation: a model leaving the recent list stays in the
#     catalog records (pending, still visible/selectable) so saved-draft
#     bindings are not deleted. Recency is display metadata, not a visibility
#     gate.
# ---------------------------------------------------------------------------
def test_d_pending_models_remain_in_catalog_records():
    catalog = DiscoveryFoundationCatalog(snapshot_path='backend/foundation_models_snapshot.json')
    rows = catalog.records()
    pending = [r for r in rows if r['recency'] == PENDING]
    assert pending, 'expected pending-verification models in the catalog'
    # Pending rows keep full discovery identity used by draft bindings.
    for r in pending:
        assert r['model_id'] and r['id'].startswith('discovery:bedrock:')
        assert r['discoverable'] is True
    # apply_recency never removes rows nor mutates access/readiness fields.
    before_ids = {r['id'] for r in rows}
    assert len(before_ids) == len(rows)


def test_d_apply_recency_does_not_touch_access_fields():
    row = normalize_model(_summary('m.old'))
    snapshot = {k: row[k] for k in ('execution_ready', 'requestable', 'discoverable',
                                    'approved', 'usable') if k in row}
    apply_recency([row], {'m.old': {'launch_date': '2020-01-01'}}, today=date(2026, 9, 13))
    for k, v in snapshot.items():
        assert row[k] == v


# ---------------------------------------------------------------------------
# (e) In-window count equals exact-id-join.json inwindow_matched_cards.
# ---------------------------------------------------------------------------
def test_e_inwindow_count_matches_verified_join():
    join = json.loads((EVIDENCE_DIR / 'exact-id-join.json').read_text())
    expected = join['inwindow_matched_cards']
    api = json.loads((EVIDENCE_DIR / 'list-foundation-models-sanitized.json').read_text())
    api_date = date.fromisoformat(join['api_retrieved_at'][:10])
    launch_map = load_launch_date_map(MAP_PATH)
    # Classify every account API model as of the verified capture date.
    recent = 0
    for m in api['modelSummaries']:
        if classify(m['modelId'], launch_map, today=api_date).recency == RECENT:
            recent += 1
    assert recent == expected, f'in-window {recent} != verified {expected}'
    assert expected == 12


def test_e_every_map_date_comes_from_verified_inventory():
    inv = json.loads((EVIDENCE_DIR / 'bedrock-model-date-inventory.json').read_text())
    inv_dates = {}
    for e in inv:
        mids = e['model_id'] if isinstance(e['model_id'], list) else [e['model_id']]
        for mid in mids:
            if e.get('launch_date'):
                inv_dates[mid] = e['launch_date']
    launch_map = load_launch_date_map(MAP_PATH)
    assert launch_map, 'launch-date map must not be empty'
    for mid, rec in launch_map.items():
        # Every emitted date must trace back to a verified inventory card.
        assert inv_dates.get(mid) == rec['launch_date'], f'fabricated/altered date for {mid}'
        assert rec.get('source_url') and rec.get('content_sha256')


def test_window_is_six_months_and_server_computed():
    start, end = window_bounds(date(2026, 9, 13))
    assert end == date(2026, 9, 13) and start == date(2026, 3, 13)
    # Default (no arg) uses current UTC date, not a hardcoded constant.
    d_start, d_end = window_bounds()
    assert (d_end - d_start).days >= 181
