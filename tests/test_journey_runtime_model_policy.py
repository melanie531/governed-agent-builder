import json
from uuid import uuid4

import pytest

from backend.catalog import PERSONAS
from backend.foundation_runs import get
from backend.journey_schema import AgentDefinition, SaveAgent
from backend.store import Store
from foundation_harness.config import digest
from foundation_harness.journey_runtime import execute
from tests.journey_support import definition, make_journey
from tests.test_journey_runtime import Gateway, Model

AU_ID = "au.anthropic.claude-sonnet-4-5-20250929-v1:0"


def saved_manifest(tmp_path, policy=None, model_id=None):
    journey, _ = make_journey(Store(str(tmp_path / "policy.sqlite")))
    if policy:
        journey.settings["model_policy"] = policy
    if model_id:
        with journey.store.tx() as db:
            row = db.select("components", where=[("id", "=", "bedrock-claude")]).fetchone()
            item = json.loads(row["body"])
            item["binding"] = {**item["binding"], "model_id": model_id}
            item["binding_digest"] = digest(item["binding"])
            db.update("components", {"body": json.dumps(item)}, where=[("id", "=", "bedrock-claude")])
    saved = journey.save(PERSONAS["alex"], SaveAgent(definition=AgentDefinition(**definition(journey)),
                         idempotency_key=uuid4().hex, deploy=False))
    with journey.store.tx() as db:
        version = journey.owned(db, PERSONAS["alex"], saved["agent_id"])[1]
        return get(db, "journey-manifest:" + version["digest"])


def run(manifest):
    gateway, model = Gateway(manifest), Model(manifest["tools"][0]["name"])
    return execute(manifest, "What is the launch plan?", "gab-" + uuid4().hex, model=model, gateway=gateway), model


def test_au_platform_pins_its_policy_in_the_manifest_and_au_binding_proceeds(tmp_path):
    manifest = saved_manifest(tmp_path, "au", AU_ID)
    assert manifest["model_policy"] == "au" and manifest["model_id"] == AU_ID
    receipt, model = run(manifest)
    assert receipt["model_id"] == AU_ID and model.requests


def test_saved_global_binding_under_au_policy_fails_closed_before_any_model_call(tmp_path):
    manifest = saved_manifest(tmp_path, "au", AU_ID)
    manifest["model_id"] = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
    gateway, model = Gateway(manifest), Model(manifest["tools"][0]["name"])
    with pytest.raises(ValueError, match=r"not allowed under the platform model policy \(au\); revise the agent's model"):
        execute(manifest, "Question", "gab-" + uuid4().hex, model=model, gateway=gateway)
    assert not model.requests and not gateway.calls


def test_unknown_manifest_policy_fails_closed(tmp_path):
    manifest = saved_manifest(tmp_path, "au", AU_ID)
    manifest["model_policy"] = "us"
    gateway, model = Gateway(manifest), Model(manifest["tools"][0]["name"])
    with pytest.raises(ValueError, match="model policy"):
        execute(manifest, "Question", "gab-" + uuid4().hex, model=model, gateway=gateway)
    assert not model.requests


def test_global_and_legacy_manifests_carry_no_policy_field_and_behave_as_today(tmp_path):
    manifest = saved_manifest(tmp_path)
    assert "model_policy" not in manifest
    receipt, model = run(manifest)
    assert receipt["model_id"] == manifest["model_id"] and model.requests
