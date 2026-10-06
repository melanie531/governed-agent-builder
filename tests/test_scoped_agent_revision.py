import json
import time
from uuid import uuid4

import pytest

from foundation_harness.config import digest
from backend.journey_schema import AgentDefinition
from scripts.scoped_agent_revision import actor_from_principal, build_request


class PrincipalRows:
    def __init__(self, body, expires):
        self.body, self.expires = body, expires

    def select(self, table, *, columns, where):
        assert table == "principals"
        assert where == [("id", "=", "user"), ("expires", ">", self.expires)]
        return self

    def fetchone(self):
        return [json.dumps(self.body)] if self.body is not None else None


def test_revision_uses_live_expiring_hosted_principal_policy():
    actor = {"id": "user", "role": "business", "workspace": "research",
             "external_allowed": False, "workspace_name": "Research studio"}
    now = time.time()
    assert actor_from_principal(PrincipalRows(actor, now), {
        "owner": "user", "workspace": "research"}, now) == actor


@pytest.mark.parametrize("actor", [
    None,
    {"id": "other", "role": "business", "workspace": "research", "external_allowed": False},
    {"id": "user", "role": "admin", "workspace": "research", "external_allowed": False},
    {"id": "user", "role": "business", "workspace": "other", "external_allowed": False},
    {"id": "user", "role": "business", "workspace": "research"},
])
def test_revision_rejects_missing_or_mismatched_live_principal(actor):
    now = time.time()
    with pytest.raises(ValueError):
        actor_from_principal(PrincipalRows(actor, now), {
            "owner": "user", "workspace": "research"}, now)


def fixture():
    fields = {"template_id": "research", "name": "Temporary Snowflake Gateway agent",
              "model_id": "model", "prompt": "List only tables the user can read.",
              "mcp_servers": ["snowflake"], "tools": ["list-tables"], "skills": [],
              "component_versions": {"model": "1", "snowflake": "1", "list-tables": "1"},
              "output_format": "text", "dataset": [], "minimum_score": 0.7}
    old = {**fields, "agent_id": uuid4().hex, "version": 6, "digest": "d" * 64,
           "foundation_artifact": {"sha256": "a" * 64}, "owner": "user", "workspace": "research"}
    row = {"id": old["agent_id"], "current_version": 6, "owner": "user", "workspace": "research"}
    platform = {"artifact": {"sha256": "b" * 64}, "gateway_force_auth_v1": True}
    return row, old, platform


def test_revision_preserves_all_authorable_fields_and_deploys_new_artifact():
    row, old, platform = fixture()
    request = build_request(row, old, platform, 6, old["digest"], "a" * 64, "b" * 64,
                            old["name"], "reviewed-request-key")
    assert request.base_version == 6 and request.deploy is True
    assert request.definition.name == old["name"]
    assert request.definition.prompt == old["prompt"]
    assert request.definition.tools == old["tools"]
    assert digest(request.definition.model_dump()) == digest({
        key: old[key] for key in AgentDefinition.model_fields})


@pytest.mark.parametrize("change", ["version", "digest", "old_artifact", "new_artifact",
                                    "capability", "name", "owner"])
def test_revision_fails_closed_on_agent_or_release_drift(change):
    row, old, platform = fixture()
    if change == "version":
        row["current_version"] = 7
    elif change == "digest":
        old["digest"] = "e" * 64
    elif change == "old_artifact":
        old["foundation_artifact"]["sha256"] = "c" * 64
    elif change == "new_artifact":
        platform["artifact"]["sha256"] = "c" * 64
    elif change == "capability":
        platform["gateway_force_auth_v1"] = False
    elif change == "name":
        old["name"] = "Another agent"
    else:
        row["owner"] = "someone-else"
    with pytest.raises(ValueError):
        build_request(row, old, platform, 6, "d" * 64, "a" * 64, "b" * 64,
                      "Temporary Snowflake Gateway agent", "reviewed-request-key")
