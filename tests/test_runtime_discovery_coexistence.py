"""Factory compatibility tests. Synthetic transport/old route, real discovery cache.
These do not claim hosted access or execute a model.
"""
import copy
from datetime import date
from pathlib import Path
from unittest.mock import Mock
from backend import model_discovery
from backend.live_catalog import configured_catalog
from backend.runtime_model_catalog import RuntimeModelCatalog
from scripts.discovery_config_overlay import with_discovery


def test_existing_runtime_route_and_recent_discovery_coexist(monkeypatch):
    root=Path(__file__).resolve().parents[1]
    account='998877665544'
    source={'approved':True,'region':'us-west-2','gateway_id':'testgateway','gateway_arn':f'arn:aws:bedrock-agentcore:us-west-2:{account}:gateway/testgateway','target_id':'testtarget',
            'bindings':[{'target_name':'testtarget','request_model':'us.anthropic.claude-haiku-4-5-20251001-v1:0','response_models':['anthropic.claude-haiku-4-5-20251001-v1:0'],'path':'/v1/messages'}],
            'exposure':{}}
    rid='model:testgateway:testtarget:us.anthropic.claude-haiku-4-5-20251001-v1:0'
    # Approval structure exercised separately by deployed adapter tests/readback.
    import backend.runtime_model_catalog as runtime
    validate=Mock();monkeypatch.setattr(runtime,'validate_source',validate)
    old={'id':rid,'model_id':source['bindings'][0]['request_model'],'kind':'model','approved':True,'fixture':False,'discoverable_workspaces':['research'],'execution_ready':False,'integration_ready':False}
    monkeypatch.setattr(RuntimeModelCatalog,'records',lambda self:[copy.deepcopy(old)])
    original={'schema_version':2,'approved':True,'binding':{'expected_account':account,'region':'us-west-2','owner_approval':'test'},'registries':[],'model_gateways':[],'runtime_model_routes':[source]}
    cfg=with_discovery(original,['research'])
    assert original['runtime_model_routes']==cfg['runtime_model_routes']
    assert 'discovery_sources' not in original
    session=Mock(region_name='us-west-2');session.client.return_value.get_caller_identity.return_value={'Account':account}
    monkeypatch.setattr(model_discovery,'utc_today',lambda:date(2026,9,13))
    catalog=configured_catalog(cfg,session=session)
    validate.assert_called_once_with(source,account,'us-west-2')
    assert isinstance(catalog.models.providers[0],RuntimeModelCatalog)
    rows=catalog.records()
    assert next(r for r in rows if r['id']==rid)==old
    discovered=catalog.discovery.records()
    ids={r['model_id'] for r in discovered}
    assert {'anthropic.claude-opus-5','anthropic.claude-sonnet-5','openai.gpt-6-astra'}<=ids
    assert old['model_id'] not in ids
    assert len(discovered)==12
    assert all(r['execution_ready'] is False and r['integration_ready'] is False for r in discovered)
    assert all(date(2026,3,13)<=date.fromisoformat(r['release_date'])<=date(2026,9,13) for r in discovered)
