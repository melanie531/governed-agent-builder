"""Summary of current caller model projections, never an authorization decision."""
import json
from pathlib import Path

_EVIDENCE = json.loads(Path(__file__).with_name('model_identity_evidence.json').read_text())
for _alias, _native in _EVIDENCE['aliases'].items():
    _proof = _EVIDENCE['evidence'][_alias]
    if (_proof['inferenceProfileId'] != _alias
            or _proof['unique_foundation_model_ids'] != [_native]):
        raise ValueError('Invalid model identity evidence')


def model_identity(item):
    raw = item.get('model_id')
    if raw:
        return _EVIDENCE['aliases'].get(raw, raw)
    return item.get('record_id') or item['id']


def listed_model(item):
    return item.get('kind') == 'model' and not item.get('parent_id') and (
        item.get('fixture') is True or item.get('recency') == 'recent'
        or item.get('catalog') == 'journey')


def model_access_summary(items):
    identity = model_identity
    total = {identity(x) for x in items if listed_model(x)}
    # Old routes contribute only to a model already eligible for the list.
    # Never transfer these summary facts back into a component's actual grant.
    rows = [x for x in items if x.get('kind') == 'model'
            and not x.get('parent_id') and identity(x) in total]
    granted = {identity(x) for x in rows if x.get('granted') is True}
    requestable = {identity(x) for x in rows if x.get('requestable') is True} - granted
    callable_ids = {identity(x) for x in rows if x.get('granted') is True
                    and x.get('usable') is True and x.get('execution_ready') is True
                    and x.get('supported') is not False and not x.get('fixture')}
    return dict(granted=len(granted), requestable=len(requestable),
                callable=len(callable_ids), available=len(total))
