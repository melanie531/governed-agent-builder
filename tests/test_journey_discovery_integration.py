"""The merged model catalog must keep the published Journey execution path."""
import copy
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.catalog import PERSONAS
from backend.model_access_summary import model_access_summary
from backend.store import Store
from tests.conftest import ORIGIN, login
from tests.journey_support import make_journey


def test_published_models_survive_recency_filter_without_changing_grants():
    model = dict(id="published", kind="model", catalog="journey", model_id="test.model",
                 granted=True, usable=True, execution_ready=True)
    assert model_access_summary([model]) == dict(granted=1, requestable=0, callable=1, available=1)
    assert model_access_summary([{**model, "granted": False, "usable": False,
                                  "requestable": True}]) == dict(granted=0, requestable=1, callable=0, available=1)


def test_discovery_and_journey_models_share_catalog_but_only_published_models_can_build(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "state.sqlite"))
    journey, _ = make_journey(store)
    discovery = dict(id="discovery:synthetic", version="1", kind="model", name="Synthetic discovery",
                     model_id="test.new-model", provider="Synthetic", description="Metadata only",
                     catalog="discovery", recency="recent", approved=True, discoverable=True,
                     discoverable_workspaces=["research"], requestable=False, discovery_only=True,
                     fixture=False, external=False, protocol="discovery-metadata",
                     execution_ready=False, integration_ready=False, supported=True)
    class Provider:
        def records(self):
            return [copy.deepcopy(discovery)]
    monkeypatch.setenv("CATALOG_MODE", "live")
    app = create_app(repository=store, journey=journey, catalog_provider=Provider(),
                     demo_mode=True, worker_enabled=False)
    with TestClient(app, base_url=ORIGIN) as client:
        login(client)
        response = client.get("/api/catalog")
        assert response.status_code == 200
        rows = {item["id"]: item for item in response.json()["items"]}
        assert {"bedrock-claude", discovery["id"]} <= rows.keys()
        assert rows["bedrock-claude"]["catalog"] == "journey"
        assert rows["bedrock-claude"]["model_id"] == "global.test.claude"
        assert rows["bedrock-claude"]["usable"] and rows["bedrock-claude"]["execution_ready"]
        assert not rows[discovery["id"]]["usable"] and not rows[discovery["id"]]["requestable"]
        options = client.get("/api/journey/options").json()
        assert discovery["id"] not in {item["id"] for item in options["choices"]["models"]}
        assert rows["bedrock-claude"]["id"] in {item["id"] for item in options["choices"]["models"]}
        assert journey.options(PERSONAS["alex"])["choices"]["models"]
