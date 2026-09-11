import copy
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.catalog import SAMPLE_DATASET

ORIGIN = "http://127.0.0.1:5187"

@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "test.sqlite"), demo_mode=True, worker_enabled=False)

@pytest.fixture
def client(app):
    with TestClient(app, base_url=ORIGIN) as client:
        yield client

def login(client, persona="alex"):
    client.headers.pop("X-CSRF-Token", None)
    response = client.post("/api/demo/session", json={"persona_id": persona}, headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf"]})
    return response

@pytest.fixture
def payload():
    return {"name": "Synthetic research companion", "foundation_id": "research", "foundation_version": "1.0.0", "model_id": "bedrock-claude", "tools": ["synthetic-search"], "skills": ["citations"], "component_versions": {"bedrock-claude": "1", "synthetic-search": "1", "citations": "1"}, "prompt": "Use synthetic evidence. Cite sources. Refuse unknown questions.", "output_format": "text", "dataset": copy.deepcopy(SAMPLE_DATASET), "rubric": {"profile": "local-deterministic", "criteria": "Ground all answers in evidence", "minimum_score": 1}, "source": "synthetic-local-only"}

def create(client, payload):
    response = client.post("/api/agents", json=payload)
    assert response.status_code == 201, response.text
    return response.json()

def enqueue(client, definition, key="idempotency-test-1"):
    response = client.post(f"/api/agents/{definition['agent_id']}/deploy-test", json={"version": definition["version"], "idempotency_key": key})
    assert response.status_code == 202, response.text
    return response.json()["job_id"]

def finish(app, client, job_id):
    for _ in range(5):
        app.state.step_job(job_id)
    response = client.get(f"/api/jobs/{job_id}")
    assert response.status_code == 200, response.text
    return response.json()
