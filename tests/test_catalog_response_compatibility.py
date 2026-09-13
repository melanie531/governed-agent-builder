"""HTTP response filtering must leave original route and saved draft readers intact.
Synthetic provider and persisted draft; no live permissions or inference.
"""
import copy
import json
from fastapi.testclient import TestClient
from backend.app import create_app
from .conftest import login, ORIGIN
from .test_live_catalog import record


def test_mixed_source_list_filters_old_route_but_detail_and_saved_draft_survive(tmp_path, monkeypatch, payload):
    old={**record(),'id':'model:qa:haiku-route','kind':'model','name':'Old Haiku',
         'model_id':'us.anthropic.claude-haiku-4-5-20251001-v1:0','version':'7'}
    recent={**old,'id':'discovery:qa:opus','model_id':'anthropic.claude-opus-5',
            'name':'Claude Opus 5','recency':'recent','discovery_only':True}
    tool=record()
    class Provider:
        def records(self):return copy.deepcopy([old,recent,tool])
    provider=Provider()
    monkeypatch.setenv('CATALOG_MODE','live')
    app=create_app(str(tmp_path/'state.sqlite'),demo_mode=True,worker_enabled=False,catalog_provider=provider)
    with TestClient(app,base_url=ORIGIN) as c:
        login(c)
        definition={**copy.deepcopy(payload),'agent_id':'old-draft','version':1,
                    'model_id':old['id'],'component_versions':{old['id']:'7'},
                    'tools':[],'skills':[],'catalog_mode':'live','digest':'synthetic'}
        with app.state.store.tx() as db:
            db.insert('agents',{'id':'old-draft','owner':'alex','workspace':'research','current_version':1,'created':1})
            db.insert('versions',{'agent':'old-draft','version':1,'digest':'synthetic','body':json.dumps(definition),'created':1})
        for query in ['', '?kind=model', '?q=Haiku']:
            r=c.get('/api/catalog'+query);assert r.status_code==200
            ids={x['id'] for x in r.json()['items']}
            assert old['id'] not in ids
            if not query:assert recent['id'] in ids and tool['id'] in ids
        for suffix in ['', '/versions/7']:
            r=c.get('/api/catalog/'+old['id']+suffix)
            assert r.status_code==200 and r.json()['model_id']==old['model_id']
        r=c.get('/api/agents/old-draft')
        assert r.status_code==200,r.text
        assert r.json()['definition']['model_id']==old['id']
        assert r.json()['definition']['component_versions']=={old['id']:'7'}
        caps=c.get('/api/capabilities');assert caps.status_code==200
        assert old['id'] in {x['id'] for x in caps.json()}
        assert provider.records()==[old,recent,tool]
        login(c,'sam')
        assert c.get('/api/catalog/'+old['id']).status_code==404
        assert c.get('/api/agents/old-draft').status_code==404
        assert old['id'] not in {x['id'] for x in c.get('/api/capabilities').json()}
