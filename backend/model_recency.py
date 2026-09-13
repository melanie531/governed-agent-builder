"""Verified launch-date recency classifier for the Bedrock discovery catalog.

Evidence source: ``backend/model_launch_dates.json`` -- a launch-date EVIDENCE
map keyed by EXACT bedrock modelId, built from the reviewer's verified official
model-card inventory (each entry carries launch_date + source_url +
content_sha256). See the map's ``_provenance`` field and the generator
``scripts/gen_model_launch_dates.py`` for how it was produced.

The account<->evidence exact-id join AND the rolling recency window are
RE-COMPUTED AT CALL TIME here, from the live model list, this evidence map, and
the current date. Nothing is baked in: no snapshot join result is consumed and
no in-window count is hardcoded. If the account list or the evidence dates
change, this recomputes.

Three classifications:
  RECENT        -- verified launch_date within [today - 6 months, today].
  OUT_OF_WINDOW -- verified launch_date, but OLDER than the window (or a future
                   date). NOT recent, but NOT "pending verification" either: the
                   date is known and verified, it is simply outside the window.
  PENDING       -- genuinely unverifiable for recency: no verified date, the
                   modelId is not matched in the evidence, or a date/ID conflict.

Only RECENT models enter the "recent" discovery list. modelLifecycle.status
(ACTIVE|LEGACY) is intentionally NOT used for recency; it remains available on
rows purely as separate lifecycle-info display.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

RECENCY_WINDOW_MONTHS = 6

# Recency classifications.
RECENT = 'recent'
OUT_OF_WINDOW = 'out_of_window'
PENDING = 'pending_verification'

# Reasons a model lands in the pending-verification queue (genuinely unverifiable).
PENDING_NO_DATE = 'no_verified_launch_date'
PENDING_CONFLICT = 'launch_date_or_id_conflict'

# Reasons a verified-date model is out of window (known date, just not recent).
OUT_OF_WINDOW_OLDER = 'launch_date_older_than_window'
OUT_OF_WINDOW_FUTURE = 'launch_date_in_future'


def _default_map_path() -> Path:
    return Path(__file__).with_name('model_launch_dates.json')


def load_launch_date_map(path: str | Path | None = None) -> dict:
    """Load the verified launch-date evidence map. Returns the id->record dict
    under ``models``. Conflicted ids (recorded under ``_conflicts``) are excluded
    so a conflicting model can never be classified recent."""
    p = Path(path) if path else _default_map_path()
    if not p.exists():
        return {}
    data = json.loads(p.read_text())
    models = data.get('models') or {}
    conflicts = set((data.get('_conflicts') or {}).keys())
    if not isinstance(models, dict):
        raise ValueError('Invalid launch-date map: models must be an object')
    return {mid: rec for mid, rec in models.items()
            if isinstance(rec, dict) and rec.get('launch_date') and mid not in conflicts}


def _months_before(anchor: date, months: int) -> date:
    """Subtract whole months from a date, clamping day-of-month if needed."""
    month_index = (anchor.year * 12 + (anchor.month - 1)) - months
    year, month = divmod(month_index, 12)
    month += 1
    day = anchor.day
    while True:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1  # clamp e.g. Aug 31 -> Feb 28


def window_bounds(today: date | None = None) -> tuple[date, date]:
    """Rolling recency window ``[today - RECENCY_WINDOW_MONTHS, today]``.
    ``today`` defaults to the current UTC date (computed here, not hardcoded)."""
    if today is None:
        today = datetime.now(timezone.utc).date()
    return _months_before(today, RECENCY_WINDOW_MONTHS), today


@dataclass
class RecencyResult:
    recency: str  # RECENT | OUT_OF_WINDOW | PENDING
    launch_date: str | None
    source_url: str | None
    content_sha256: str | None
    reason: str | None  # pending_reason or out-of-window reason; None when recent

    @property
    def is_recent(self) -> bool:
        return self.recency == RECENT

    def as_row_fields(self) -> dict:
        # ``pending_reason`` is kept for backward compatibility but is only set
        # for genuinely-pending rows; out-of-window rows carry ``recency_reason``.
        return {
            'recency': self.recency,
            'launch_date': self.launch_date,
            'launch_date_source': self.source_url,
            'launch_date_sha256': self.content_sha256,
            'recency_reason': self.reason,
            'pending_reason': self.reason if self.recency == PENDING else None,
        }


def classify(model_id: str, launch_map: dict, today: date | None = None) -> RecencyResult:
    """Classify one modelId using ONLY verified dates. Recompute, never baked in.

    - RECENT        : verified date within the rolling window.
    - OUT_OF_WINDOW : verified date, older than the window or in the future.
    - PENDING       : no verified date / unmatched id / conflict.
    """
    start, end = window_bounds(today)
    rec = launch_map.get(model_id)
    if not rec:
        return RecencyResult(PENDING, None, None, None, PENDING_NO_DATE)
    raw = rec.get('launch_date')
    if not raw:
        return RecencyResult(PENDING, None, rec.get('source_url'),
                             rec.get('content_sha256'), PENDING_NO_DATE)
    try:
        ld = date.fromisoformat(raw)
    except (TypeError, ValueError):
        return RecencyResult(PENDING, raw, rec.get('source_url'),
                             rec.get('content_sha256'), PENDING_CONFLICT)
    src = rec.get('source_url')
    sha = rec.get('content_sha256')
    if ld > end:
        # A verified but future-dated card is out of window, not pending.
        return RecencyResult(OUT_OF_WINDOW, raw, src, sha, OUT_OF_WINDOW_FUTURE)
    if ld < start:
        return RecencyResult(OUT_OF_WINDOW, raw, src, sha, OUT_OF_WINDOW_OLDER)
    return RecencyResult(RECENT, raw, src, sha, None)


def apply_recency(rows: list[dict], launch_map: dict | None = None,
                  today: date | None = None) -> list[dict]:
    """Enrich discovery rows in place with recency + launch-date fields.

    Only pure metadata is added; access/approval/readiness is untouched. Rows
    that are not models are left unchanged."""
    if launch_map is None:
        launch_map = load_launch_date_map()
    for row in rows:
        if row.get('kind') != 'model':
            continue
        result = classify(row.get('model_id'), launch_map, today=today)
        row.update(result.as_row_fields())
    return rows
