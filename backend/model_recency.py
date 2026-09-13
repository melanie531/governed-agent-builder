"""Verified launch-date recency classifier for the Bedrock discovery catalog.

Source of truth: ``backend/model_launch_dates.json`` — a launch-date map keyed by
EXACT bedrock modelId, built from the reviewer's verified official model-card
inventory (each entry carries launch_date + source_url + content_sha256). See the
map's ``_provenance`` field and ``scripts``/generator for how it was produced.

Recency is a REAL rolling window, not a lifecycle proxy: a model is "recent"
ONLY IF it has a verified launch_date within ``[today - 6 months, today]`` where
``today`` is computed at call time (server-side), never hardcoded. Everything
else — missing date, future date, unmatched modelId, or a date/ID conflict —
goes to the "pending verification" queue and is NEVER surfaced as recent.

modelLifecycle.status (ACTIVE|LEGACY) is intentionally NOT used for recency. It
remains available on rows purely as separate lifecycle-info display.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

RECENCY_WINDOW_MONTHS = 6

# Recency classifications.
RECENT = 'recent'
PENDING = 'pending_verification'

# Reasons a model lands in the pending-verification queue.
PENDING_NO_DATE = 'no_verified_launch_date'
PENDING_FUTURE = 'launch_date_in_future'
PENDING_OUT_OF_WINDOW = 'launch_date_older_than_window'
PENDING_CONFLICT = 'launch_date_or_id_conflict'


def _default_map_path() -> Path:
    return Path(__file__).with_name('model_launch_dates.json')


def load_launch_date_map(path: str | Path | None = None) -> dict:
    """Load the verified launch-date map. Returns the id->record dict under
    ``models``. Conflicted ids (recorded under ``_conflicts``) are excluded so a
    conflicting model can never be classified recent."""
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
    recency: str  # RECENT | PENDING
    launch_date: str | None
    source_url: str | None
    content_sha256: str | None
    pending_reason: str | None

    def as_row_fields(self) -> dict:
        return {
            'recency': self.recency,
            'launch_date': self.launch_date,
            'launch_date_source': self.source_url,
            'launch_date_sha256': self.content_sha256,
            'pending_reason': self.pending_reason,
        }


def classify(model_id: str, launch_map: dict, today: date | None = None) -> RecencyResult:
    """Classify one modelId as recent vs pending using ONLY verified dates."""
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
        return RecencyResult(PENDING, raw, src, sha, PENDING_FUTURE)
    if ld < start:
        return RecencyResult(PENDING, raw, src, sha, PENDING_OUT_OF_WINDOW)
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
