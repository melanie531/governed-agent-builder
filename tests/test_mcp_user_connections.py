import json
import secrets
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import pytest

from backend.catalog import PERSONAS
from backend.foundation_runs import put
from backend.live_catalog import grant_scope
from tests.conftest import login
from tests.test_mcp_credentials import enable
from tests.test_mcp_iam import BODY, ENDPOINT
from tests.test_mcp_onboarding import BODY as ONBOARD, setup, start, drain, approve, detail
from tests.test_mcp_user_configuration import native, AUTH
from backend.mcp_user_connections import UserConnections


@pytest.fixture
def connected_server(setup):
    enable(setup)
    cloud, *_ = native()
    tokens = {user: secrets.token_urlsafe(32) for user in ("alex", "sam", "admin")}
    sessions, completed, calls = {}, set(), []
    lost = {"complete": False}
    def token(**args):
        calls.append(("token", args))
        user = args["workloadIdentityToken"]
        if user in completed:
            return {"accessToken": tokens[user] if user in tokens else secrets.token_urlsafe(32)}
        uri = args.get("sessionUri") or "urn:ietf:params:oauth:request_uri:" + uuid4().hex
        sessions[uri] = user
        return {"authorizationUrl": "https://provider.example.com/authorize?" + urlencode({
                    "state": args.get("customState", ""), "session_id": uri}),
                "sessionUri": uri, "sessionStatus": "IN_PROGRESS"}
    def complete(**args):
        calls.append(("complete", args))
        assert sessions[args["sessionUri"]] == args["userIdentifier"]["userToken"]
        completed.add(args["userIdentifier"]["userToken"])
        if lost["complete"]:
            raise TimeoutError("Native acknowledgement lost")
    cloud._data = SimpleNamespace(
        get_workload_access_token_for_jwt=lambda **kw: {"workloadAccessToken": kw["userToken"]},
        get_resource_oauth2_token=token, complete_resource_token_auth=complete)
    setup[2].oauth_cloud = cloud
    setup[2].auth = SimpleNamespace(**vars(AUTH), session=lambda request: (
        {"sub": request.state.persona["id"]}, {"access_token": tokens[request.state.persona["id"]]}))
    response = setup[0].post("/api/admin/mcp/iam-credentials", json={**BODY, "user_authorization": True})
    assert response.status_code == 200, response.text
    created = start(setup, {**ONBOARD, "endpoint": ENDPOINT, "connection_id": response.json()["connection_id"],
                            "workspaces": ["research", "operations"]})
    drain(setup[2], created["job_id"])
    approved = approve(setup, created)
    assert approved.status_code == 202, approved.text
    drain(setup[2], approved.json()["job_id"])
    sid = detail(setup, created)["catalog_id"]
    with setup[1].tx() as db:
        for user in ("alex", "sam"):
            db.insert("grants", {"persona": user, "component": sid})
            put(db, grant_scope(PERSONAS[user], sid), True)
    login(setup[0])
    return setup[0], sid, tokens, calls, lost


def begin(client, sid):
    response = client.post(f"/api/mcp/user-connections/{sid}/authorize",
                           json={"idempotency_key": uuid4().hex})
    assert response.status_code == 200, response.text
    flow = response.json()
    assert flow["phase"] == "CONSENT_REQUIRED"
    query = parse_qs(urlsplit(flow["authorization_url"]).query)
    return flow, {"state": query["state"][0], "session_uri": query["session_id"][0]}


def test_native_consent_is_bound_to_current_user_and_tokens_never_reach_browser(connected_server, setup):
    client, sid, tokens, calls, _ = connected_server
    assert client.get("/api/mcp/user-connections").json()["items"][0]["phase"] == "NOT_CONNECTED"
    flow, callback = begin(client, sid)
    login(client, "sam")
    assert client.post("/api/mcp/user-connections/complete", json=callback).status_code == 403
    assert not [c for c in calls if c[0] == "complete"]
    assert client.get("/api/mcp/user-connections/flows/" + flow["id"]).status_code == 404
    login(client, "alex")
    response = client.post("/api/mcp/user-connections/complete", json=callback)
    assert response.status_code == 200 and response.json()["phase"] == "CONNECTED"
    assert client.post("/api/mcp/user-connections/complete", json=callback).json() == response.json()
    assert len([c for c in calls if c[0] == "complete"]) == 1
    assert client.get("/api/mcp/user-connections").json()["items"][0]["phase"] == "CONNECTED"
    login(client, "sam")
    assert client.get("/api/mcp/user-connections").json()["items"][0]["phase"] == "NOT_CONNECTED"
    with setup[1].tx() as db:
        stored = json.dumps([dict(r) for r in db.select("settings")] + [dict(r) for r in db.select("audit")])
    assert all(token not in stored + response.text for token in tokens.values())


def test_callback_rejects_state_or_native_session_substitution(connected_server):
    client, sid, _, calls, _ = connected_server
    _, callback = begin(client, sid)
    for altered in ({**callback, "state": secrets.token_urlsafe(32)},
                    {**callback, "session_uri": "urn:ietf:params:oauth:request_uri:" + uuid4().hex}):
        assert client.post("/api/mcp/user-connections/complete", json=altered).status_code in (403, 404)
    assert not [c for c in calls if c[0] == "complete"]


def test_status_check_before_consent_preserves_the_ability_to_complete(connected_server):
    client, sid, _, _, _ = connected_server
    flow, callback = begin(client, sid)
    checked = client.post("/api/mcp/user-connections/flows/" + flow["id"] + "/check", json={})
    assert checked.status_code == 200 and checked.json()["phase"] == "CONSENT_REQUIRED"
    assert checked.json()["authorization_url"] == flow["authorization_url"]
    assert client.post("/api/mcp/user-connections/complete", json=callback).json()["phase"] == "CONNECTED"


def test_uncertain_complete_requires_explicit_token_reconciliation_without_replay(connected_server):
    client, sid, _, calls, lost = connected_server
    flow, callback = begin(client, sid)
    lost["complete"] = True
    response = client.post("/api/mcp/user-connections/complete", json=callback)
    assert response.status_code == 200 and response.json()["phase"] == "NEEDS_CHECK"
    assert client.post("/api/mcp/user-connections/complete", json=callback).json()["phase"] == "NEEDS_CHECK"
    assert len([c for c in calls if c[0] == "complete"]) == 1
    checked = client.post("/api/mcp/user-connections/flows/" + flow["id"] + "/check", json={})
    assert checked.status_code == 200 and checked.json()["phase"] == "CONNECTED"
    assert len([c for c in calls if c[0] == "complete"]) == 1


def test_consent_start_is_idempotent_and_does_not_repeat_native_authorization(connected_server):
    client, sid, _, calls, _ = connected_server
    body = {"idempotency_key": uuid4().hex}
    path = f"/api/mcp/user-connections/{sid}/authorize"
    first = client.post(path, json=body)
    assert first.status_code == 200
    assert client.post(path, json=body).json() == first.json()
    assert client.get("/api/mcp/user-connections/requests/" + body["idempotency_key"]).json() == first.json()
    assert len([c for c in calls if c[0] == "token"]) == 1
    assert client.post(path, json={**body, "force_authentication": True}).status_code == 409


def test_callback_requires_current_capability_grant_and_csrf(connected_server, setup):
    client, sid, _, calls, _ = connected_server
    _, callback = begin(client, sid)
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.post("/api/mcp/user-connections/complete", json=callback).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    with setup[1].tx() as db:
        db.delete("grants", where=[("persona", "=", "alex"), ("component", "=", sid)])
    assert client.post("/api/mcp/user-connections/complete", json=callback).status_code == 403
    assert not [c for c in calls if c[0] == "complete"]


def test_agent_invocation_requires_consent_and_passes_identity_only_to_private_transport(connected_server, setup):
    import time
    from fastapi import HTTPException
    from backend.foundation_runs import get
    from backend.journey_schema import AgentDefinition, InvokeAgent, SaveAgent
    from tests.journey_support import definition

    client, sid, tokens, _, _ = connected_server
    journey = client.app.state.journey
    with setup[1].tx() as db:
        item = json.loads(db.select("components", where=[("id", "=", sid)]).fetchone()["body"])
        tools = item["default_tool_ids"]
        for tool in tools:
            db.insert("grants", {"persona": "alex", "component": tool}, ignore=True)
            put(db, grant_scope(PERSONAS["alex"], tool), True)
    draft = definition(journey)
    draft.update(mcp_servers=[sid], tools=tools)
    draft["component_versions"] = {cid: "1" for cid in
        [draft["model_id"], sid, *tools, *draft["skills"]]}
    saved = journey.save(PERSONAS["alex"], SaveAgent(definition=AgentDefinition(**draft),
        idempotency_key=uuid4().hex, deploy=False))
    before = journey.detail(PERSONAS["alex"], saved["agent_id"])
    assert before["user_authorization_required"]
    assert not before["preopen_provider_tab"]
    with pytest.raises(HTTPException, match="My connections"):
        journey.action(PERSONAS["alex"], saved["agent_id"],
            InvokeAgent(version=1, idempotency_key=uuid4().hex, input="SELECT CURRENT_USER()"), "invoke")
    _, callback = begin(client, sid)
    assert client.post("/api/mcp/user-connections/complete", json=callback).json()["phase"] == "CONNECTED"
    assert not journey.detail(PERSONAS["alex"], saved["agent_id"])["preopen_provider_tab"]
    state_id = uuid4().hex
    with setup[1].tx() as db:
        current = journey.owned(db, PERSONAS["alex"], saved["agent_id"])[1]
        manifest = get(db, "journey-manifest:" + current["digest"])
        assert manifest["tools"][0]["user_authorization"] is True
        db.execute("CREATE TABLE hosted_sessions(id_hash TEXT PRIMARY KEY, subject TEXT, access_token TEXT, expires REAL)")
        db.insert("hosted_sessions", {"id_hash": "session-one", "subject": "alex",
                                     "access_token": tokens["alex"], "expires": time.time() + 300})
        db.insert("principals", {"id": "alex", "body": json.dumps(PERSONAS["alex"]), "expires": time.time() + 300}, upsert=True)
        db.insert("job_authority", {"id": state_id, "session_hash": "session-one"})
    journey.hosted = True
    journey.auth = SimpleNamespace(verify=lambda token, purpose: {"sub": "alex", "exp": time.time() + 900},
                                   membership=lambda claims: PERSONAS["alex"])
    dispatched = []
    journey.cloud = SimpleNamespace(invoke=lambda *args, **kwargs: dispatched.append(kwargs) or {"output": "query result"})
    state = {"id": state_id, "owner": "alex", "agent": saved["agent_id"], "version": 1,
             "definition_digest": current["digest"], "kind": "invoke"}
    assert journey.invocation(state, current, {}, "Query", "invoke", False) == {"output": "query result"}
    assert dispatched == [{"user_token": tokens["alex"]}]
    journey.auth.verify = lambda token, purpose: {"sub": "alex", "exp": time.time() + 100}
    with pytest.raises(HTTPException, match="about to expire"):
        journey.invocation(state, current, {}, "Query", "short-access-token", False)
    with setup[1].tx() as db:
        persisted = json.dumps([dict(r) for table in ("settings", "audit", "jobs", "versions") for r in db.select(table)])
        db.update("hosted_sessions", {"expires": 0}, where=[("id_hash", "=", "session-one")])
    assert all(token not in persisted + json.dumps(manifest) for token in tokens.values())
    with pytest.raises(HTTPException, match="expired"):
        journey.invocation(state, current, {}, "Query", "invoke-again", False)
    assert len(dispatched) == 1


def test_gateway_consent_completes_for_its_user_without_bff_token_acquisition(connected_server, setup):
    from backend.foundation_runs import get
    from backend.mcp_user_connections import SESSION_GRANT
    from foundation_harness.config import digest
    from tests.test_mcp_gateway_oauth import GATEWAY

    client, sid, tokens, calls, _ = connected_server
    service = setup[2]
    user = service.oauth_cloud.gateway_provider("test-studio-mcp-oauth-example", "test-studio", AUTH, ["read"])
    with setup[1].tx() as db:
        config = get(db, "mcp-onboarding")
        config["oauth_gateway"] = {**GATEWAY, "issuer": AUTH.issuer, "client_id": AUTH.client_id}
        put(db, "mcp-onboarding", config)
        server = json.loads(db.select("components", where=[("id", "=", sid)]).fetchone()["body"])
        cid = server["binding"]["user_authorization"]["connection_id"]
        connection = get(db, "mcp-auth:" + cid)
        connection["user_authorization"] = user
        connection["configuration"] = {"credentialProviderType": "OAUTH", "credentialProvider": {
            "oauthCredentialProvider": {"providerArn": "arn:aws:bedrock-agentcore:us-west-2:123456789012:"
                "token-vault/default/oauth2credentialprovider/" + user["provider_name"],
                "scopes": ["read"], "grantType": "AUTHORIZATION_CODE", "defaultReturnUrl": user["return_url"]}}}
        put(db, "mcp-auth:" + cid, connection)
        server["binding"]["user_authorization"]["configuration_digest"] = digest(user)
        server["binding"]["gateway_id"] = GATEWAY["gateway_id"]
        server["binding_digest"] = digest(server["binding"])
        db.update("components", {"body": json.dumps(server)}, where=[("id", "=", sid)])
        tool_id = server["default_tool_ids"][0]
        tool = json.loads(db.select("components", where=[("id", "=", tool_id)]).fetchone()["body"])
        tool["binding"].update(gateway_id=GATEWAY["gateway_id"], gateway_auth="COGNITO", gateway_url=GATEWAY["gateway_url"])
        tool["binding_digest"] = digest(tool["binding"])
        db.update("components", {"body": json.dumps(tool)}, where=[("id", "=", tool_id)])
        db.insert("grants", {"persona": "alex", "component": tool_id}, ignore=True)
        put(db, grant_scope(PERSONAS["alex"], tool_id), True)
    users = UserConnections(service)
    uri = "urn:ietf:params:oauth:request_uri:" + uuid4().hex
    url = "https://bedrock-agentcore.us-west-2.amazonaws.com/identities/oauth2/authorize?" + urlencode({"request_uri": uri})
    definition = {"mcp_servers": [sid], "tools": [tool_id]}
    challenge = {"session_uri": uri, "authorization_url": url, "tool_name": tool["binding"]["name"]}
    with setup[1].tx() as db:
        assert users.required(db, PERSONAS["alex"], definition)  # No preemptive BFF consent gate.
        assert users.preopen_provider_tab(db, PERSONAS["alex"], definition)
        flow = users.capture_gateway(db, PERSONAS["alex"], definition, challenge, source_job_id="query-one")
    assert "source_job_id" not in flow
    resumed = []
    client.app.state.journey.resume_connected_flow = lambda actor, flow_id, *, session_hash=None: (
        resumed.append((actor["id"], flow_id, session_hash)) or {"job_id": "query-two"})
    # Native completion is the sole data-plane OAuth operation used by the BFF.
    completed = []
    service.oauth_cloud._data = SimpleNamespace(complete_resource_token_auth=lambda **kw: completed.append(kw))
    login(client, "sam")
    assert client.post("/api/mcp/user-connections/complete", json={"session_uri": uri}).status_code == 403
    assert resumed == []
    login(client, "alex")
    from backend.hosted_auth import SESSION_COOKIE, sha
    client.cookies.set(SESSION_COOKIE, "studio-session-one")
    result = client.post("/api/mcp/user-connections/complete", json={"session_uri": uri})
    assert result.status_code == 200 and result.json()["phase"] == "CONNECTED", result.text
    assert resumed == [("alex", flow["id"], None)]
    assert client.post("/api/mcp/user-connections/complete", json={"session_uri": uri}).json() == result.json()
    assert completed == [{"sessionUri": uri, "userIdentifier": {"userToken": tokens["alex"]}}]
    assert not calls
    assert client.get("/api/mcp/user-connections/flows/" + flow["id"]).json()["phase"] == "CONNECTED"
    with setup[1].tx() as db:
        assert not users.preopen_provider_tab(db, PERSONAS["alex"], definition)
        assert users.owned(db, PERSONAS["alex"], flow["id"])[0]["source_job_id"] == "query-one"
        grant = get(db, SESSION_GRANT + digest(["alex", sid, sha("studio-session-one")]))
        assert grant["phase"] == "CONNECTED"


def test_gateway_reauthorization_is_due_after_one_hour_but_cannot_start_outside_mcp_call(connected_server, setup):
    import time
    from backend.foundation_runs import get
    from backend.mcp_user_connections import GRANT
    from foundation_harness.config import digest
    from tests.test_mcp_gateway_oauth import GATEWAY

    client, sid, tokens, _, _ = connected_server
    service = setup[2]
    user = service.oauth_cloud.gateway_provider("test-studio-mcp-oauth-example", "test-studio", AUTH, ["read"])
    with setup[1].tx() as db:
        config = get(db, "mcp-onboarding")
        config["oauth_gateway"] = {**GATEWAY, "issuer": AUTH.issuer, "client_id": AUTH.client_id}
        put(db, "mcp-onboarding", config)
        server = json.loads(db.select("components", where=[("id", "=", sid)]).fetchone()["body"])
        cid = server["binding"]["user_authorization"]["connection_id"]
        connection = get(db, "mcp-auth:" + cid)
        connection["user_authorization"] = user
        connection["configuration"] = {"credentialProviderType": "OAUTH", "credentialProvider": {
            "oauthCredentialProvider": {"providerArn": "arn:aws:bedrock-agentcore:us-west-2:123456789012:"
                "token-vault/default/oauth2credentialprovider/" + user["provider_name"],
                "scopes": ["read"], "grantType": "AUTHORIZATION_CODE", "defaultReturnUrl": user["return_url"]}}}
        put(db, "mcp-auth:" + cid, connection)
        server["binding"]["user_authorization"]["configuration_digest"] = digest(user)
        server["binding"]["gateway_id"] = GATEWAY["gateway_id"]
        server["binding_digest"] = digest(server["binding"])
        db.update("components", {"body": json.dumps(server)}, where=[("id", "=", sid)])
        grant_key = GRANT + digest(["alex", sid])
        put(db, grant_key, {"phase": "CONNECTED", "configuration_digest": digest(user),
                            "checked_at": time.time() - 3601})

    users = UserConnections(service)
    with setup[1].tx() as db:
        reauthorization = users.gateway_reauthorization(db, PERSONAS["alex"], {"mcp_servers": [sid]})
        assert len(reauthorization) == 1
        assert reauthorization[0]["server_id"] == sid
        assert reauthorization[0]["due"] is True
        assert reauthorization[0]["due_at"] <= time.time()
    requests = []
    service.oauth_cloud._data = SimpleNamespace(
        get_workload_access_token_for_jwt=lambda **kw: requests.append(("workload", kw)),
        get_resource_oauth2_token=lambda **kw: requests.append(("token", kw)),
        complete_resource_token_auth=lambda **kw: requests.append(("complete", kw)))
    response = client.post(f"/api/mcp/user-connections/{sid}/authorize", json={
        "idempotency_key": uuid4().hex, "force_authentication": True})
    assert response.status_code == 409
    assert "Gateway MCP tool call" in response.text
    assert requests == []
    with setup[1].tx() as db:
        reauthorization = users.gateway_reauthorization(db, PERSONAS["alex"], {"mcp_servers": [sid]})
        assert reauthorization[0]["due"] is True


def test_gateway_reauthorization_is_once_per_studio_session(connected_server, setup):
    import time
    from backend.foundation_runs import get
    from backend.mcp_user_connections import GRANT, SESSION_GRANT
    from foundation_harness.config import digest
    from tests.test_mcp_gateway_oauth import GATEWAY

    client, sid, _, _, _ = connected_server
    service = setup[2]
    user = service.oauth_cloud.gateway_provider("test-studio-mcp-oauth-example", "test-studio", AUTH, ["read"])
    with setup[1].tx() as db:
        config = get(db, "mcp-onboarding")
        config["oauth_gateway"] = {**GATEWAY, "issuer": AUTH.issuer, "client_id": AUTH.client_id}
        put(db, "mcp-onboarding", config)
        server = json.loads(db.select("components", where=[("id", "=", sid)]).fetchone()["body"])
        cid = server["binding"]["user_authorization"]["connection_id"]
        connection = get(db, "mcp-auth:" + cid)
        connection["user_authorization"] = user
        connection["configuration"] = {"credentialProviderType": "OAUTH", "credentialProvider": {
            "oauthCredentialProvider": {"providerArn": "arn:aws:bedrock-agentcore:us-west-2:123456789012:"
                "token-vault/default/oauth2credentialprovider/" + user["provider_name"],
                "scopes": ["read"], "grantType": "AUTHORIZATION_CODE", "defaultReturnUrl": user["return_url"]}}}
        put(db, "mcp-auth:" + cid, connection)
        server["binding"]["user_authorization"]["configuration_digest"] = digest(user)
        server["binding"]["gateway_id"] = GATEWAY["gateway_id"]
        server["binding_digest"] = digest(server["binding"])
        db.update("components", {"body": json.dumps(server)}, where=[("id", "=", sid)])
        grant = {"phase": "CONNECTED", "configuration_digest": digest(user), "checked_at": time.time()}
        put(db, GRANT + digest(["alex", sid]), grant)
        put(db, SESSION_GRANT + digest(["alex", sid, "studio-session-one"]), grant)

    users = UserConnections(service)
    with setup[1].tx() as db:
        definition = {"mcp_servers": [sid]}
        assert users.gateway_reauthorization(db, PERSONAS["alex"], definition,
                                              session_hash="studio-session-one")[0]["due"] is False
        assert users.gateway_reauthorization(db, PERSONAS["alex"], definition,
                                              session_hash="studio-session-two")[0]["due"] is True
        assert users.gateway_reauthorization(db, PERSONAS["alex"], definition,
                                              session_hash="studio-session-one")[0]["due_at"] > time.time()
        put(db, SESSION_GRANT + digest(["alex", sid, "studio-session-two"]), grant)
        assert users.gateway_reauthorization(db, PERSONAS["alex"], definition,
                                              session_hash="studio-session-one")[0]["due"] is False
        assert users.gateway_reauthorization(db, PERSONAS["alex"], definition,
                                              session_hash="studio-session-two")[0]["due"] is False
        put(db, SESSION_GRANT + digest(["alex", sid, "studio-session-one"]),
            {**grant, "checked_at": time.time() - 3601})
        assert users.gateway_reauthorization(db, PERSONAS["alex"], definition,
                                              session_hash="studio-session-one")[0]["due"] is True
        assert users.gateway_reauthorization(db, PERSONAS["alex"], definition,
                                              session_hash="studio-session-two")[0]["due"] is False
