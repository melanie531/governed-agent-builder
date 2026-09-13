"""Model-only summary over caller projections; no AWS calls or grants."""
from backend.model_access_summary import model_access_summary


def test_models_only_dedupe_and_all_execution_gates():
    def row(id, model='a', **kw):
        return dict(id=id, kind='model', model_id=model, recency='recent',
                    granted=True, usable=True, execution_ready=True, **kw)
    a=row('route-a')
    denied={**row('route-b','b'),'usable':False,'execution_binding':{'status':'verified'}}
    not_ready={**row('route-c','c'),'execution_ready':False,'execution_binding':{'status':'verified'}}
    requestable={**row('route-d','d'),'granted':False,'requestable':True}
    models=[a,{**a,'id':'second-route-a'},denied,not_ready,requestable]
    noise=[{**a,'kind':'mcp_server'},{**a,'kind':'skill'},
           {**a,'id':'legacy','model_id':'old','recency':None},
           {**a,'id':'old','model_id':'old2','recency':'out_of_window'}]
    assert model_access_summary(models+noise)==dict(granted=3,requestable=1,callable=1,available=4)


def test_no_combining_permissions_and_readiness_across_routes():
    base=dict(kind='model',model_id='a',recency='recent',execution_ready=True)
    rows=[dict(base,id='1',granted=True,usable=False),dict(base,id='2',granted=False,usable=True)]
    assert model_access_summary(rows)['callable']==0
