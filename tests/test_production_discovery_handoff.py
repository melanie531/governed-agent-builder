"""Production-cache contract tests. Official documents and real API snapshot; no inference."""
from datetime import date

import pytest

from backend import model_discovery as md, model_recency as mr


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


@pytest.mark.parametrize('today', [date(2026,9,13), date(2026,9,26), date(2026,9,27)])
def test_real_snapshot_cache_through_production_factory_and_http(tmp_path, monkeypatch, today):
    from unittest.mock import Mock
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend.live_catalog import configured_catalog
    from scripts.discovery_config_overlay import with_discovery
    from tests.conftest import login, ORIGIN
    existing={'schema_version':2,'approved':True,'binding':{'expected_account':'998877665544','region':'us-west-2','owner_approval':'local test identity only'},'registries':[],'model_gateways':[]}
    cfg=with_discovery(existing,['research'])
    assert 'discovery_sources' not in existing
    assert cfg['registries']==existing['registries']
    monkeypatch.setattr(md,'utc_today',lambda:today)
    # Feed discovery and the exact-ID launch-date join have independent clocks.
    # Keep both on the tested date; retain the real rolling-window calculation.
    window_bounds=mr.window_bounds
    snapshot_day=today
    monkeypatch.setattr(mr,'window_bounds',lambda today=None:window_bounds(snapshot_day if today is None else today))
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
        expected={
            'openai.gpt-6-astra', 'openai.gpt-5.6-terra', 'openai.gpt-5.6-luna', 'openai.gpt-5.6-sol',
            'anthropic.claude-fable-5', 'anthropic.claude-fable-5-1', 'anthropic.claude-opus-5',
            'anthropic.claude-opus-4-8', 'anthropic.claude-opus-4-7', 'anthropic.claude-sonnet-5',
            'xai.grok-4.6',
        }
        # This reviewed model launched March 26: included at the six-calendar-
        # month boundary on September 26, excluded starting September 27.
        if today<=date(2026,9,26):
            expected.add('writer.palmyra-vision-7b')
        assert set(by_id)==expected
        assert len(rows)==len(expected)
        for row in rows:
            assert row['execution_ready'] is False and row['usable'] is False
            assert row['granted'] is False and row['requestable'] is False
            assert row['recency']=='recent'
            assert md.window_start(today)<=date.fromisoformat(row['release_date'])<=today
            assert md.window_start(today)<=date.fromisoformat(row['launch_date'])<=today
