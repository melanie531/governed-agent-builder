"""Build an unfiltered reviewed discovery cache by exact API/model-card ID join.
Window filtering remains in DiscoveryCatalogSource at read time. No AWS writes.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def build(api, inventory):
    by_id = {}
    for card in inventory:
        ids = card['model_id'] if isinstance(card['model_id'], list) else [card['model_id']]
        for mid in ids:
            by_id.setdefault(mid, []).append(card)
    records, pending = [], []
    for model in api['modelSummaries']:
        mid = model['modelId']; cards = by_id.get(mid, [])
        if len(cards) != 1:
            pending.append({'model_id':mid,'reason':'unmatched' if not cards else 'conflicting_sources'});continue
        card = cards[0]
        if not card.get('launch_date') or not card.get('source_url') or not card.get('content_sha256'):
            pending.append({'model_id':mid,'reason':'missing_verified_date_or_source'});continue
        if card.get('launch_date_precision') == 'month_only' or not re.search(r'\b\d{1,2},\s*\d{4}\b', card.get('evidence_quote') or ''):
            pending.append({'model_id':mid,'reason':'date_precision_unverified'});continue
        records.append({'record_id':mid,'model_id':mid,'vendor':model['providerName'],'model_name':model['modelName'],
            'capabilities':['input:'+x for x in model.get('inputModalities',[])]+['output:'+x for x in model.get('outputModalities',[])],
            'release_date':card['launch_date'],'source_url':card['source_url'],'source_content_sha256':card['content_sha256'],
            'supported_endpoints':card.get('endpoint',[]),'verified':True,
            'review':{'status':'approved','reviewed_by':'Owner-provided official-date evidence','reviewed_at':card['retrieved_at']},
            'region_availability':'listed_in_account_snapshot','account_api_retrieved_at':api['retrieved_at'],
            'model_lifecycle':model.get('modelLifecycle',{}),'runtime_protocol':'unknown'})
    return {'schema_version':1,'official_review_status':'complete','source':{'name':'Official model cards joined to ListFoundationModels','url':'https://docs.aws.amazon.com/bedrock/latest/userguide/model-cards.html'},'generated_at':api['retrieved_at'],'records':records,'pending_verification':pending}

if __name__ == '__main__':
    api=json.loads((ROOT/'backend/foundation_models_snapshot.json').read_text())
    inventory=json.loads((ROOT/'docs/review-data/bedrock-model-date-inventory.json').read_text())
    output=ROOT/'backend/reviewed_model_discovery_cache.json'
    output.write_text(json.dumps(build(api,inventory),indent=2))
    print('Cache generated; date window is evaluated when serving, not at generation.')
