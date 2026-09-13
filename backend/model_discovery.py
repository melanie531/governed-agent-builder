"""Recent-model discovery from owner-verified official review records.

DISCOVERY ONLY. This module is deliberately NOT wired into /api/catalog,
``configured_catalog()`` or ``LiveCatalog``. Discovery is distinct from
Gateway connection, authorization/entitlement and execution readiness:
every record this module emits is fixed to ``execution_ready=False`` and
``execution_binding={'status': 'unverified', ...}``.

Input records come from an owner-verified official review feed (vendor,
model name, capabilities, official release date, source URL). This module
ships NO built-in model list: official model names and release dates are
being verified by the owner and must never be fabricated here. Tests use
explicitly synthetic records only.

Window semantics: rolling six *calendar* months (UTC dates, injectable
``today``), not a 180-day approximation. Invalid records fail loud or are
returned with an explicit exclusion reason; there is no degraded fallback
catalog.
"""
from datetime import date, datetime, timezone

DISCOVERY_ORIGIN = 'Official model review records'
WINDOW_MONTHS = 6

# Vendor routing policy (Melanie's directive): Claude/OpenAI models are only
# served through Bedrock Runtime; Gemini is an external provider; Amazon
# Nova and Bedrock Mantle are explicitly NOT allowed in this discovery feed.
VENDOR_ROUTES = {
    'Anthropic': {'provider_api': 'bedrock-runtime', 'external': False},
    'OpenAI': {'provider_api': 'bedrock-runtime', 'external': False},
    'Google': {'provider_api': 'external', 'external': True},
}


def utc_today():
    """Current UTC calendar date (injectable in discover/lookups via today=)."""
    return datetime.now(timezone.utc).date()


def window_start(today):
    """Inclusive start of the rolling six-calendar-month window.

    Calendar-month arithmetic with day clamping (e.g. 2026-08-31 -> 2026-02-28),
    NOT a 180-day simplification.
    """
    month_index = today.year * 12 + (today.month - 1) - WINDOW_MONTHS
    year, month = divmod(month_index, 12)
    month += 1
    # Clamp the day to the last valid day of the target month.
    for day in range(today.day, 27, -1):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return date(year, month, min(today.day, 28))


def parse_release_date(value):
    """Strict ISO ``YYYY-MM-DD`` official release date; anything else is invalid."""
    if not isinstance(value, str):
        raise ValueError('release date must be an ISO YYYY-MM-DD string')
    parsed = datetime.strptime(value, '%Y-%m-%d').date()
    if value != parsed.isoformat():
        raise ValueError('release date must be canonical ISO YYYY-MM-DD')
    return parsed


def discover(records, today=None):
    """Partition official review records into discovered/excluded.

    Returns ``{'discovered': [...], 'excluded': [...]}``. Every excluded entry
    carries an explicit machine-readable ``reason``; nothing is silently
    dropped and nothing is fabricated to fill the list.

    Structural corruption of the feed itself (missing/duplicate record_id)
    fails loud with ``ValueError`` rather than producing a degraded catalog.
    """
    today = utc_today() if today is None else today
    start = window_start(today)
    _check_feed_identity(records)
    discovered, excluded = [], []

    def exclude(record, reason, detail=''):
        excluded.append({'record_id': record.get('record_id'),
                         'reason': reason, 'detail': detail})

    for record in records:
        if record.get('synthetic') is not True and record.get('verified') is not True:
            exclude(record, 'unverified_record',
                    'record is neither an explicit synthetic test fixture nor an '
                    'owner-verified official record; refusing silent discovery')
            continue
        vendor = record.get('vendor')
        if vendor not in VENDOR_ROUTES:
            exclude(record, 'unknown_vendor',
                    f'vendor {vendor!r} is not an approved discovery vendor '
                    '(Anthropic/OpenAI via bedrock-runtime, Google external only; '
                    'Amazon Nova and Bedrock Mantle are not allowed)')
            continue
        source_url = record.get('source_url')
        if not isinstance(source_url, str) or not source_url.startswith('https://'):
            exclude(record, 'missing_source_url',
                    'an https:// official review source URL is required')
            continue
        release_raw = record.get('release_date')
        if release_raw is None:
            exclude(record, 'missing_release_date',
                    'official release date not verified; record cannot be discovered')
            continue
        try:
            release = parse_release_date(release_raw)
        except ValueError as error:
            exclude(record, 'invalid_release_date', str(error))
            continue
        if release > today:
            exclude(record, 'future_release_date',
                    f'release date {release.isoformat()} is after today {today.isoformat()}')
            continue
        if release < start:
            exclude(record, 'outside_six_month_window',
                    f'window starts {start.isoformat()} (six calendar months before {today.isoformat()})')
            continue
        discovered.append(_projection(record))
    return {'discovered': discovered, 'excluded': excluded}


def _check_feed_identity(records):
    seen = set()
    for record in records:
        record_id = record.get('record_id') if isinstance(record, dict) else None
        if not isinstance(record_id, str) or not record_id:
            raise ValueError('record_id is required on every official review record')
        if record_id in seen:
            raise ValueError(f'Duplicate record_id in review feed: {record_id}')
        seen.add(record_id)


def _projection(record):
    """Discovery row: official metadata verbatim plus non-executable status.

    Discovery is distinct from Gateway connection, authorization and
    execution readiness — these flags are fixed, never derived from input.
    """
    route = VENDOR_ROUTES[record['vendor']]
    item = dict(record)
    item['capabilities'] = list(record.get('capabilities', []))
    item.update(origin=DISCOVERY_ORIGIN, discovery_only=True,
                provider_api=route['provider_api'], external=route['external'],
                execution_ready=False, integration_ready=False,
                execution_binding={'status': 'unverified', 'last_checked': None},
                entitlement='unverified', gateway_enumeration='NotConnected')
    return item


class BindingLookup:
    """Resolver for historical draft/binding model references.

    Separate from the new-discovery list: rolling-window filtering never
    deletes entries here. Unknown references resolve to an explicit
    ``unverified_reference`` row instead of being silently dropped, and no
    resolution is ever execution-ready.
    """

    def __init__(self, records, today=None):
        today = utc_today() if today is None else today
        self._start = window_start(today)
        self._today = today
        _check_feed_identity(records)
        self._records = {record['record_id']: dict(record) for record in records}

    def resolve(self, record_id):
        base = {'record_id': record_id, 'binding_retained': True,
                'execution_ready': False, 'integration_ready': False,
                'execution_binding': {'status': 'unverified', 'last_checked': None}}
        record = self._records.get(record_id)
        if record is None:
            base.update(status='unverified_reference', in_discovery_window=False,
                        detail='binding references a model with no verified official '
                               'record; retained, never silently deleted')
            return base
        in_window = False
        release_raw = record.get('release_date')
        if isinstance(release_raw, str):
            try:
                release = parse_release_date(release_raw)
                in_window = self._start <= release <= self._today
            except ValueError:
                in_window = False
        base.update(record, status='resolved', in_discovery_window=in_window)
        # Readiness flags are fixed by this module, never taken from input:
        # a poisoned record must not resolve as executable.
        base.update(execution_ready=False, integration_ready=False,
                    execution_binding={'status': 'unverified', 'last_checked': None},
                    binding_retained=True)
        return base


def binding_lookup(records, today=None):
    """Build the historical-binding resolver (separate from ``discover``)."""
    return BindingLookup(records, today=today)
