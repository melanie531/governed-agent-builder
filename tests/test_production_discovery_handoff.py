"""Production-cache contract tests. Official documents and real API snapshot; no inference."""
from datetime import date
from backend import model_discovery as md


def test_vendor_does_not_invent_runtime_or_external_endpoint():
    for vendor, endpoints in [('Google',['bedrock-runtime']),('Writer',['bedrock-runtime']),('xAI',['bedrock-runtime']),('Anthropic',['bedrock-mantle'])]:
        record={'record_id':'test-'+vendor,'vendor':vendor,'model_name':'explicit-test-record','release_date':'2026-08-01','source_url':'https://docs.aws.amazon.com/bedrock/latest/userguide/model-cards.html','synthetic':True,'supported_endpoints':endpoints}
        result=md.discover([record],today=date(2026,9,13))
        assert len(result['discovered'])==1, result
        row=result['discovered'][0]
        assert row['supported_endpoints']==endpoints
        assert row.get('provider_api')!='external'
        if endpoints==['bedrock-mantle']:
            assert row.get('provider_api')!='bedrock-runtime'
        assert row['execution_ready'] is False


def test_real_snapshot_cache_through_production_factory_and_http(tmp_path, monkeypatch):
    from pathlib import Path
    from unittest.mock import Mock
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend.live_catalog import configured_catalog
    from scripts.discovery_config_overlay import with_discovery
    from tests.conftest import login, ORIGIN
    root=Path(__file__).resolve().parents[1]
    existing={'schema_version':2,'approved':True,'binding':{'expected_account':'998877665544','region':'us-west-2','owner_approval':'local test identity only'},'registries':[],'model_gateways':[]}
    cfg=with_discovery(existing,['research'])
    assert 'discovery_sources' not in existing
    assert cfg['registries']==existing['registries']
    monkeypatch.setattr(md,'utc_today',lambda:date(2026,9,13))
    session=Mock(region_name='us-west-2')
    session.client.return_value.get_caller_identity.return_value={'Account':'998877665544'}
    provider=configured_catalog(cfg,session=session)
    monkeypatch.setenv('CATALOG_MODE','live')
    app=create_app(str(tmp_path/'state.sqlite'),demo_mode=True,worker_enabled=False,catalog_provider=provider)
    with TestClient(app,base_url=ORIGIN) as c:
        login(c)
        response=c.get('/api/catalog')
        assert response.status_code==200
        rows=response.json()['items'];by_id={x.get('model_id'):x for x in rows}
        for mid in ['anthropic.claude-opus-5','anthropic.claude-sonnet-5','openai.gpt-6-astra']:
            assert mid in by_id
            assert by_id[mid]['execution_ready'] is False
            assert by_id[mid]['usable'] is False
        assert len(rows)==12
        assert all(date(2026,3,13)<=date.fromisoformat(x['release_date'])<=date(2026,9,13) for x in rows)

