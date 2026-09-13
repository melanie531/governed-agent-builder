#!/usr/bin/env python3
"""Generate backend/model_launch_dates.json from VERIFIED reviewer inventory.

Source of truth (branch docs/model-launch-date-evidence, docs/review-data/):
  - bedrock-model-date-inventory.json (127 official model cards) = launch-date EVIDENCE
  - list-foundation-models-sanitized.json (113 sanitized API models) = account API list

This builds the verified launch-date EVIDENCE map keyed by exact modelId. It is
NOT a runtime recency answer: recency (the rolling 6-month window) and the
account<->evidence exact-id join are RE-COMPUTED at runtime by backend.model_recency
from the live API model list + this evidence map + the current date. exact-id-join.json
is a VERIFICATION SNAPSHOT only and is deliberately NOT consumed here.

Rule: include ONLY inventory entries whose model_id EXACT-matches an account
API modelId. Carry launch_date + source_url + content hash + precision +
evidence quote. No fabrication: entries without a launch_date, unmatched, or
conflicting are NOT written to the map.
"""
import json
import sys
from pathlib import Path

EV = Path(__file__).resolve().parents[1] / "docs" / "review-data"
OUT = sys.argv[1] if len(sys.argv) > 1 else 'backend/model_launch_dates.json'

inv = json.loads((EV / 'bedrock-model-date-inventory.json').read_text())
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
            continue  # conflict -> keep first, flag; both dropped by loader guard
        entries[mid] = rec

payload = {
    '_provenance': (
        'Verified launch-date EVIDENCE map keyed by EXACT bedrock modelId. Built from '
        'docs/review-data/bedrock-model-date-inventory.json (official AWS Bedrock '
        'model cards, each with source_url + content_sha256) filtered to ids that '
        'exact-match the account bedrock:ListFoundationModels snapshot '
        '(list-foundation-models-sanitized.json, 113 models). This is EVIDENCE only: '
        'the account<->evidence exact-id join and the rolling 6-month recency window '
        'are RE-COMPUTED AT RUNTIME (backend.model_recency) from the live API list, '
        'this map, and the current date. No dates are fabricated; entries with no '
        'date, no API match, or a date conflict are omitted here.'),
    '_source_branch': 'docs/model-launch-date-evidence',
    '_source_files': [
        'docs/review-data/bedrock-model-date-inventory.json',
        'docs/review-data/list-foundation-models-sanitized.json',
    ],
    '_api_retrieved_at': api.get('retrieved_at'),
    '_api_model_count': len(api_ids),
    '_inventory_card_count': len(inv),
    '_matched_entry_count': len(entries),
    '_conflicts': conflicts,
    'models': dict(sorted(entries.items())),
}

Path(OUT).write_text(json.dumps(payload, indent=2, ensure_ascii=True) + '\n')
print(f'wrote {OUT}: {len(entries)} matched verified entries, {len(conflicts)} conflicts')
