"""Registry-only connections retain Foundation Library and blocked draft composition."""
import json
from backend.live_catalog import LiveCatalog, Sources
from .test_builder_catalog import Provider, live


def test_registry_only_status_no_fixture_models():
    p = Provider(); p.items = [p.items[-1]]
    catalog = LiveCatalog(Sources([]), Sources([p]))
    assert catalog.source_status()['Registry']['connection_state'] == 'connected'
    assert catalog.source_status()['ModelGateway']['connection_state'] == 'NotConnected'
    assert [x['kind'] for x in catalog.records()] == ['tool']


def test_registry_only_draft_and_foundations(live, payload):
    app, client, provider = live
    tool = provider.items[-1]; provider.items = [tool]
    snapshot = client.get('/api/catalog').json()
    assert snapshot['mode'] == 'live'
    assert len(snapshot['items']) == 1 and not snapshot['items'][0]['fixture']
    options = client.get('/api/build-options', params={'foundation_id':'research'}).json()
    assert len(options['foundations']) == 2 and options['choices']['models'] == []
    payload.update(model_id='',tools=[tool['id']],skills=[],component_versions={tool['id']:tool['version']})
    result = client.post('/api/agents',json=payload)
    assert result.status_code == 201, result.text
    saved = result.json()
    assert saved['component_versions'] == payload['component_versions']
    assert not saved['readiness']['deployable']
    assert any(x['code']=='model_missing' for x in saved['readiness']['issues'])
    with app.state.store.tx() as db:
        assert db.select('jobs', count=True).fetchone()[0] == 0
