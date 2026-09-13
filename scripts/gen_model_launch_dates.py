#!/usr/bin/env python3
"""Generate backend/model_launch_dates.json from VERIFIED reviewer inventory.

Source of truth (branch docs/model-launch-date-evidence, docs/review-data/):
  - bedrock-model-date-inventory.json (127 official model cards)
  - exact-id-join.json (authoritative exact-ID join to the account API)
  - list-foundation-models-sanitized.json (113 sanitized API models)

Rule: include ONLY inventory entries whose model_id EXACT-matches an account
API modelId. Carry launch_date + source_url + content hash + precision +
evidence quote. No fabrication: entries without a launch_date, unmatched, or
conflicting are NOT written to the map (they become pending at runtime).
"""
import json
import sys
from pathlib import Path

EV = Path(__file__).resolve().parents[1] / "docs" / "review-data"
OUT = sys.argv[1] if len(sys.argv) > 1 else 'backend/model_launch_dates.json'

inv = json.loads((EV / 'bedrock-model-date-inventory.json').read_text())
join = json.loads((EV / 'exact-id-join.json').read_text())
api = json.loads((EV / 'list-foundation-models-sanitized.json').read_text())

api_ids = set(m['modelId'] for m in api['modelSummaries'])


def ids_of(entry):
    mid = entry['model_id']
    return mid if isinstance(mid, list) else [mid]


entries = {}
conflicts = {}
for e in inv:
    ld = e.get('launch_date')
    if not ld:
        continue  # no verified date -> not in map (pending at runtime)
    for mid in ids_of(e):
        if mid not in api_ids:
            continue  # unmatched -> not in map
        rec = {
            'launch_date': ld,
            'launch_date_precision': e.get('launch_date_precision'),
            'launch_date_type': e.get('launch_date_type'),
            'source_url': e.get('source_url'),
            'content_sha256': e.get('content_sha256'),
            'evidence_quote': e.get('evidence_quote'),
            'inventory_status': e.get('status'),
        }
        if mid in entries and entries[mid]['launch_date'] != ld:
            conflicts.setdefault(mid, [entries[mid]['launch_date']]).append(ld)
            continue  # conflict -> keep first, flag; both routed pending by loader guard
        entries[mid] = rec

payload = {
    '_provenance': (
        'Verified launch-date map keyed by EXACT bedrock modelId. Built from '
        'docs/review-data/bedrock-model-date-inventory.json (official AWS Bedrock '
        'model cards, each with source_url + content_sha256) joined via exact '
        'modelId match against the account bedrock:ListFoundationModels snapshot '
        '(list-foundation-models-sanitized.json, 113 models). Only exact-match '
        'entries with a verified launch_date are included. No dates are fabricated; '
        'entries with no date, no API match, or a date conflict are omitted here '
        'and routed to the pending-verification queue at runtime.'),
    '_source_branch': 'docs/model-launch-date-evidence',
    '_source_files': [
        'docs/review-data/bedrock-model-date-inventory.json',
        'docs/review-data/exact-id-join.json',
        'docs/review-data/list-foundation-models-sanitized.json',
    ],
    '_api_retrieved_at': join.get('api_retrieved_at'),
    '_api_model_count': len(api_ids),
    '_inventory_card_count': len(inv),
    '_matched_entry_count': len(entries),
    '_conflicts': conflicts,
    'models': dict(sorted(entries.items())),
}

Path(OUT).write_text(json.dumps(payload, indent=2, ensure_ascii=True) + '\n')
print(f'wrote {OUT}: {len(entries)} matched verified entries, {len(conflicts)} conflicts')
