"""Cached recent-model discovery catalog source.

Reads the owner-maintained discovery cache (an on-disk JSON snapshot of the
official model review feed) and adapts reviewed records into catalog rows the
existing ``/api/catalog`` projection can serve.

Contract:
- The cache is REQUIRED once this source is configured. A missing, corrupt or
  unexpected-schema cache fails loud (``IntegrationNotConfigured``); there is
  no silent empty-success fallback.
- Records ship with source/review metadata (official ``source_url``, review
  provenance). Unreviewed records are excluded with an explicit reason, never
  silently served.
- Window semantics come from :mod:`backend.model_discovery` (rolling six
  calendar months, backend-side). Filtering never deletes historical bindings;
  use ``model_discovery.binding_lookup`` for those.
- Discovery is distinct from account/region availability, Runtime protocol
  compatibility, Gateway enumeration, authorization/entitlement and execution
  readiness. Facts the cache does not verify stay ``'unknown'`` /
  ``'unverified'``; readiness booleans are pinned False and can never be set
  by cache content (poisoned-input safe).
- Discovery rows are never requestable and never create exposure or grants.
"""
import json
from datetime import date
from pathlib import Path

from .aws_adapter import IntegrationNotConfigured
from . import model_discovery
from . import model_recency
from .live_catalog import revision
from .model_recency import classify, load_launch_date_map

CACHE_SCHEMA_VERSION = 1
REVIEW_STATUSES = {'pending', 'complete'}


def load_discovery_cache(path):
    """Load and validate the on-disk discovery cache; fail loud on any defect."""
    cache_path = Path(path)
    try:
        raw = cache_path.read_text()
    except OSError:
        raise IntegrationNotConfigured(
            f'Model discovery cache missing at {cache_path}; the owner-reviewed '
            'discovery cache is required — no empty fallback') from None
    try:
        body = json.loads(raw)
    except ValueError:
        raise IntegrationNotConfigured(
            f'Model discovery cache at {cache_path} is not valid JSON; refusing '
            'a degraded discovery catalog') from None
    if not isinstance(body, dict) or body.get('schema_version') != CACHE_SCHEMA_VERSION:
        raise IntegrationNotConfigured(
            'Model discovery cache schema version unsupported; expected '
            f'schema_version={CACHE_SCHEMA_VERSION}')
    if body.get('official_review_status') not in REVIEW_STATUSES:
        raise IntegrationNotConfigured(
            "Model discovery cache must declare official_review_status "
            "('pending' or 'complete')")
    source = body.get('source')
    if (not isinstance(source, dict) or not isinstance(source.get('name'), str)
            or not source['name'].strip() or not isinstance(source.get('url'), str)
            or not source['url'].startswith('https://')):
        raise IntegrationNotConfigured(
            'Model discovery cache requires an https feed source with a name')
    if not isinstance(body.get('records'), list):
        raise IntegrationNotConfigured('Model discovery cache records must be a list')
    return body


def _parse_today(today):
    if today is None:
        return model_discovery.utc_today()
    if isinstance(today, date):
        return today
    return model_discovery.parse_release_date(today)


def _reviewed(record):
    review = record.get('review')
    return (isinstance(review, dict) and review.get('status') == 'approved'
            and isinstance(review.get('reviewed_by'), str) and review['reviewed_by'].strip()
            and isinstance(review.get('reviewed_at'), str) and review['reviewed_at'].strip())


class DiscoveryCatalogSource:
    """CatalogProvider adapter over the cached official model review feed."""

    def __init__(self, source_id, cache_path, scope, today=None):
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError('Discovery source_id required')
        if (not isinstance(scope, dict) or not isinstance(scope.get('workspaces'), list)
                or not scope['workspaces']
                or any(not isinstance(w, str) or not w.strip() for w in scope['workspaces'])):
            raise ValueError('Discovery scope with explicit workspaces required')
        self.source_id = source_id
        self.cache_path = cache_path
        self.scope = scope
        self._today = today
        self.last_excluded = []
        self._last_status = None

    def records(self):
        body = load_discovery_cache(self.cache_path)
        today = _parse_today(self._today)
        reviewed, excluded = [], []
        for record in body['records']:
            if not isinstance(record, dict):
                raise IntegrationNotConfigured('Model discovery cache record corrupt')
            if not _reviewed(record):
                excluded.append({'record_id': record.get('record_id'),
                                 'reason': 'unreviewed_record',
                                 'detail': 'record lacks an approved owner review; '
                                           'refusing unreviewed discovery'})
                continue
            reviewed.append(record)
        try:
            result = model_discovery.discover(reviewed, today=today)
        except ValueError as error:
            raise IntegrationNotConfigured(
                f'Model discovery cache feed corrupt: {error}') from None
        excluded.extend(result['excluded'])
        self.last_excluded = excluded
        rows = [self._row(item, body) for item in result['discovered']]
        self._last_status = {
            'connection_state': 'connected',
            'official_data_status': body['official_review_status'],
            'source': dict(body['source']),
            'generated_at': body.get('generated_at'),
            'discovered': len(rows), 'excluded': len(excluded),
            'reason': ('Official model list verification pending; only owner-reviewed '
                       'records are served' if body['official_review_status'] == 'pending'
                       else '')}
        return rows

    def status(self):
        return dict(self._last_status) if self._last_status else {
            'connection_state': 'NotConnected',
            'reason': 'Discovery cache not read yet'}

    def _row(self, item, body):
        record_id = item['record_id']
        digest = revision({k: item.get(k) for k in (
            'record_id', 'vendor', 'model_name', 'capabilities',
            'release_date', 'source_url', 'review')})
        row = {
            'id': f'discovery:{self.source_id}:{record_id}',
            'record_id': record_id,
            'kind': 'model',
            'name': item.get('model_name') or record_id,
            'description': 'Recently released model (owner-reviewed discovery); '
                           'access, authorization and execution are separate steps',
            'provider': item.get('vendor', ''),
            'capabilities': list(item.get('capabilities', [])),
            'release_date': item.get('release_date'),
            'model_id': item.get('model_id', record_id),
            'supported_endpoints': list(item.get('supported_endpoints', [])),
            'account_api_retrieved_at': item.get('account_api_retrieved_at'),
            'endpoint_configuration': 'unconfigured',
            'source_url': item.get('source_url'),
            'review': dict(item['review']),
            'version': digest, 'source_revision': digest,
            'origin': model_discovery.DISCOVERY_ORIGIN,
            'source_type': 'discovery_cache',
            'official_data_status': body['official_review_status'],
            'owner': self.scope.get('owner', 'Not declared'),
            'data_handling': self.scope.get('data_handling', 'Not assessed'),
            'discoverable_workspaces': list(self.scope['workspaces']),
            'approved': True, 'discoverable': True, 'fixture': False,
            'external': bool(item.get('external', False)),
            'protocol': 'metadata-only', 'supported': True,
            'discovery_only': True,
            # Separate, individually recorded facts; unknown stays unknown.
            'region_availability': item['region_availability']
                if isinstance(item.get('region_availability'), str) else 'unknown',
            'runtime_protocol': item['runtime_protocol']
                if isinstance(item.get('runtime_protocol'), str) else 'unknown',
            'refreshed_at': body.get('generated_at'),
        }
        # Pinned last: cache content can NEVER make a discovery row requestable,
        # entitled, enumerated or executable (poisoned-input safe).
        row.update(requestable=False, integration_ready=False, execution_ready=False,
                   execution_binding={'status': 'unverified', 'last_checked': None},
                   entitlement='unverified', gateway_enumeration='NotConnected')
        # ADDITIVE recency layer: if the cache record carries an exact Bedrock
        # modelId that matches the reviewer-verified launch-date evidence map
        # (backend/model_launch_dates.json), attach the three-state recency
        # classification (recent | out_of_window | pending_verification) plus
        # the verified launch_date + source. This is a SEPARATE, exact-id join
        # recomputed at call time; it never overrides the feed's own
        # release_date/source_url or any readiness/access pin above. Rows with
        # no matching verified evidence are left exactly as the feed built them.
        model_id = item.get('model_id') or record_id
        if model_id:
            result = classify(model_id, self._launch_map())
            if result.launch_date is not None or result.recency != model_recency.PENDING:
                # Only attach when there is real verified evidence to add; keep
                # existing keys (model_id passthrough for the exact-id join).
                row.setdefault('model_id', model_id)
                for key, value in result.as_row_fields().items():
                    row.setdefault(key, value)
        return row

    def _launch_map(self):
        # Loaded once per source instance; the exact-id join is recomputed per
        # classify() call against the current date.
        cached = getattr(self, '_launch_map_cache', None)
        if cached is None:
            cached = load_launch_date_map()
            self._launch_map_cache = cached
        return cached
