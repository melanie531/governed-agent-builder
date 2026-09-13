from tests.conftest import login


def test_new_tool_request_is_private_idempotent_and_never_grants_catalog_access(client, app):
    login(client)
    with app.state.store.tx() as db:
        grants = list(db.select("grants"))
    body = {"title": " CRM ", "details": "Read synthetic account summaries.", "idempotency_key": "tool-request-test"}
    created = client.post("/api/tool-requests", json=body)
    assert created.status_code == 201
    item = created.json()
    assert item["title"] == "CRM"
    assert item["status"] == "SUBMITTED"
    assert client.post("/api/tool-requests", json=body).json()["id"] == item["id"]
    assert client.post("/api/tool-requests", json={**body, "title": "Different tool"}).status_code == 409
    assert client.get("/api/requests").json() == []
    login(client, "sam")
    assert client.get("/api/tool-requests").json() == []
    reply = {"status": "FULFILLED", "response": "The new connector is available in the catalog.", "version": 1}
    assert client.post(f"/api/admin/tool-requests/{item['id']}/response", json=reply).status_code == 403
    login(client, "admin")
    assert client.get("/api/tool-requests").json()[0]["id"] == item["id"]
    assert client.post(f"/api/admin/tool-requests/{item['id']}/response", json=reply).status_code == 200
    assert client.post(f"/api/admin/tool-requests/{item['id']}/response", json=reply).status_code == 409
    login(client)
    assert client.get("/api/tool-requests").json()[0]["status"] == "FULFILLED"
    with app.state.store.tx() as db:
        assert list(db.select("grants")) == grants


def test_new_tool_requests_require_auth_csrf_and_valid_freeform_content(client):
    body = {"title": "CRM lookup", "idempotency_key": "tool-request-test"}
    assert client.get("/api/tool-requests").status_code == 401
    assert client.post("/api/tool-requests", json=body).status_code == 403
    login(client)
    assert client.post("/api/tool-requests", json={**body, "component_id": "bedrock-claude"}).status_code == 422
    assert client.post("/api/tool-requests", json={**body, "title": "     "}).status_code == 422
    client.headers.pop("X-CSRF-Token")
    assert client.post("/api/tool-requests", json=body).status_code == 403
