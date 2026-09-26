"""Validate and publish operator-owned MCP profiles; never reads credential values."""
import argparse
import json
from pathlib import Path

from backend.dynamo_store import DynamoStore
from backend.foundation_runs import get, put
from backend.mcp_servers import validate_profile
from foundation_harness.config import digest
from scripts.deployment_target import target_arguments
from scripts.snowflake_platform import SnowflakeTarget, NoRetrySession, Journal


def configure(target, config):
    settings = target.state["journeyPlatform"]
    if set(config) != {"enabled", "profiles"} or config["enabled"] is not True or not 1 <= len(config["profiles"]) <= 10:
        raise ValueError("Enabled MCP profiles are required")
    profiles = [validate_profile(p, settings) for p in config["profiles"]]
    if len({p["id"] for p in profiles}) != len(profiles):
        raise ValueError("Duplicate MCP profile")
    if {p["provisioning_secret_arn"] for p in profiles} != set(settings["mcp_creation"]["provisioning_secret_arns"]):
        raise ValueError("Profile secrets must exactly match deployed worker permissions")
    if {p["credential_provider_arn"] for p in profiles} != set(settings["mcp_creation"]["credential_provider_arns"]):
        raise ValueError("Profile credential providers must exactly match deployed worker permissions")
    value = {"enabled": True, "profiles": profiles}
    store = DynamoStore(target.state["app"]["outputs"]["StateTable"], target.session.resource("dynamodb"))
    def read(_):
        with store.tx() as db:
            return {"digest": digest(value)} if get(db, "mcp-platform") == value else None
    def write(_):
        with store.tx() as db:
            if get(db, "mcp-platform") not in (None, value):
                raise ValueError("Existing MCP configuration differs; use a reviewed configuration migration")
            put(db, "mcp-platform", value)
        return {"digest": digest(value)}
    return Journal(target).run("mcp-profile-" + digest(value), value, write, read)


def main():
    parser = argparse.ArgumentParser()
    target_arguments(parser)
    parser.add_argument("--config", required=True, type=Path, help="Nonsecret approved profile JSON")
    args = parser.parse_args()
    target = SnowflakeTarget(args.expected_account, args.profile, args.region, args.state)
    target.session = NoRetrySession(target.session, target)
    print(json.dumps(configure(target, json.loads(args.config.read_text()))))


if __name__ == "__main__":
    main()
