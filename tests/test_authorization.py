import pytest
from .conftest import ORIGIN, login, create, enqueue, finish

@pytest.mark.parametrize("method,path", [("get","/api/agents"),("get","/api/build-options"),("get","/api/agents/unknown"),("get","/api/jobs/unknown"),("get","/api/agents/unknown/export"),("post","/api/agents"),("post","/api/agents/unknown/versions"),("post","/api/agents/unknown/deploy-test"),("post","/api/agents/unknown/invoke"),("get","/api/requests"),("get","/api/capabilities"),("get","/api/admin/catalog"),("get","/api/admin/audit"),("post","/api/admin/grants"),("post","/api/admin/policy"),("post","/api/admin/requests/unknown/decision"),("post","/api/admin/catalog/components/bedrock-claude")])
def test_every_protected_route_requires_session(client, method, path):
    response = getattr(client, method)(path, headers={"Origin": ORIGIN})
    assert response.status_code == 401

@pytest.mark.parametrize("method,path,body", [("get","/api/admin/catalog",None),("get","/api/admin/audit",None),("post","/api/admin/grants",{"persona_id":"alex","component_id":"restricted-insights","enabled":True}),("post","/api/admin/policy",{"require_judge":False,"minimum_score":0}),("post","/api/admin/catalog/components/bedrock-claude",{"approved":False}),("post","/api/admin/requests/unknown/decision",{"approve":True,"reason":"Please approve"})])
def test_business_cannot_act_as_admin(client, method, path, body):
    login(client)
    kwargs = {"json":body} if method == "post" else {}
    assert getattr(client,method)(path,**kwargs).status_code == 403

@pytest.mark.parametrize("persona", ["sam", "admin"])
@pytest.mark.parametrize("operation", ["read","revise","deploy","job","export","invoke","list"])
def test_cross_identity_workspace_idor(client, payload, persona, operation):
    login(client)
    definition = create(client,payload)
    job_id = enqueue(client,definition)
    login(client,persona)
    agent_path = f"/api/agents/{definition['agent_id']}"
    if operation == "read": r=client.get(agent_path)
    elif operation == "revise": r=client.post(agent_path+"/versions",json={**payload,"base_version":1})
    elif operation == "deploy": r=client.post(agent_path+"/deploy-test",json={"version":1,"idempotency_key":"other-identity-key"})
    elif operation == "job": r=client.get(f"/api/jobs/{job_id}")
    elif operation == "export": r=client.get(agent_path+"/export")
    elif operation == "invoke": r=client.post(agent_path+"/invoke",json={"version":1,"input":"Aurora"})
    else:
        assert client.get('/api/agents').json() == []
        return
    assert r.status_code in (403,404), r.text

@pytest.mark.parametrize("field,value", [("owner","admin"),("workspace","operations"),("role","admin"),("persona_id","admin")])
def test_payload_identity_cannot_override_session(client,payload,field,value):
    login(client)
    assert client.post('/api/agents',json={**payload,field:value}).status_code == 422

@pytest.mark.parametrize("mutation", ["unknown-model","wrong-kind","ungranted-tool","incompatible-tool","external-policy","unknown-version","extra-version"])
def test_forged_or_incompatible_components(client,payload,mutation):
    login(client)
    if mutation == "unknown-model":
        payload['model_id']='forged';payload['component_versions']['forged']='1';del payload['component_versions']['bedrock-claude']
    elif mutation == "wrong-kind":
        payload['tools']=['bedrock-openai'];del payload['component_versions']['synthetic-search'];payload['component_versions']['bedrock-openai']='1'
    elif mutation in ("ungranted-tool","incompatible-tool"):
        payload['tools'].append('restricted-insights');payload['component_versions']['restricted-insights']='1'
        if mutation=='incompatible-tool':
            login(client,'admin');client.post('/api/admin/grants',json={'persona_id':'alex','component_id':'restricted-insights','enabled':True});login(client)
            payload['foundation_id']='knowledge'
    elif mutation == "external-policy":
        login(client,'sam');payload['model_id']='external-gemini';del payload['component_versions']['bedrock-claude'];payload['component_versions']['external-gemini']='1'
    elif mutation == "unknown-version":payload['component_versions']['bedrock-claude']='wrong'
    else:payload['component_versions']['phantom']='1'
    assert client.post('/api/agents',json=payload).status_code in (403,409,422)

def test_catalog_intersection_persona_and_foundation(client):
    login(client)
    alex=client.get('/api/build-options?foundation_id=research').json()['choices']
    assert {m['id'] for m in alex['models']}=={'bedrock-claude','bedrock-openai'}
    assert 'restricted-insights' not in {t['id'] for t in alex['tools']}
    login(client,'sam')
    sam=client.get('/api/build-options?foundation_id=knowledge').json()['choices']
    assert {m['id'] for m in sam['models']}=={'bedrock-claude'}
    assert {s['id'] for s in sam['skills']}=={'concise'}

def test_session_cookie_rotation_and_csrf(client):
    response=login(client)
    cookie=response.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=strict' in cookie
    old=client.cookies.get('gab_session')
    assert client.post('/api/requests',json={'component_id':'restricted-insights','reason':'Need insights'},headers={'X-CSRF-Token':'wrong'}).status_code==403
    login(client,'sam')
    assert client.cookies.get('gab_session')!=old
    client.cookies.clear();client.cookies.set('gab_session',old)
    assert client.get('/api/me').status_code==401

@pytest.mark.parametrize("headers", [{'Origin':'https://malicious.example'},{'Origin':ORIGIN,'Sec-Fetch-Site':'cross-site'},{'Origin':ORIGIN,'Host':'evil.example'}])
def test_origin_and_dns_rebinding(client,headers):
    assert client.post('/api/demo/session',json={'persona_id':'admin'},headers=headers).status_code in (400,403)

def test_body_limit_and_invalid_login(client):
    assert client.post('/api/demo/session',content='x'*70000,headers={'Origin':ORIGIN}).status_code==413
    assert client.post('/api/demo/session',json={'persona_id':'alex','role':'admin'},headers={'Origin':ORIGIN}).status_code==422

def test_revocation_blocks_old_agent_invoke_retry_and_worker(app,client,payload):
    login(client);d=create(client,payload);j=enqueue(client,d);assert finish(app,client,j)['stage']=='PASS'
    login(client,'admin');assert client.post('/api/admin/grants',json={'persona_id':'alex','component_id':'synthetic-search','enabled':False}).status_code==200
    login(client)
    path=f"/api/agents/{d['agent_id']}"
    assert client.post(path+'/invoke',json={'version':1,'input':'Aurora'}).status_code==403
    assert client.post(path+'/deploy-test',json={'version':1,'idempotency_key':'retry-revoked'}).status_code==403
    # Even a persisted queued job revalidates authorization.
    with app.state.store.tx() as db:db.execute("UPDATE jobs SET stage='VALIDATING' WHERE id=?",(j,))
    assert finish(app,client,j)['stage']=='NEEDS_CHANGES'

def test_expired_session_and_client_headers(client,app):
    login(client)
    assert client.get('/api/me',headers={'X-User-Role':'admin','X-Workspace':'platform'}).json()['persona']['role']=='business'
    with app.state.store.tx() as db:db.execute('UPDATE sessions SET expires=0')
    assert client.get('/api/me').status_code==401
