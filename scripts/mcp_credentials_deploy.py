"""Enable generic credential setup on the already authorized existing Studio."""
import argparse
import copy
import json
import time

from backend.dynamo_store import DynamoStore
from backend.foundation_runs import get, put
from foundation_harness.config import digest
from infra.mcp_onboarding import configure_gateway
from infra.resource_tags import validate_resource_tags
from scripts.deployment_target import target_arguments
from scripts.mcp_onboarding_deploy import Release, evidence_name

PREFIX = "governed-agent-builder-serverless"


def prepare(release):
    settings = copy.deepcopy(release.state["journeyPlatform"])
    settings["mcp_onboarding"]["credential_prefix"] = PREFIX
    release.target.save("journeyPlatform", settings)
    print("Pinned generic credential namespace for existing Studio", flush=True)


def gateway(release):
    cf = release.client("cloudformation")
    stack_id = release.state["journeyStack"]["id"]
    stack = cf.describe_stacks(StackName=stack_id)["Stacks"][0]
    if stack_id.split("/")[1] != "governed-agent-builder-journey" or stack["StackStatus"] not in ("UPDATE_COMPLETE", "CREATE_COMPLETE"):
        raise ValueError("Existing Journey stack is not ready")
    previous = cf.get_template(StackName=stack_id)["TemplateBody"]
    previous = json.loads(previous) if isinstance(previous, str) else previous
    proposed = copy.deepcopy(previous)
    policies = proposed["Resources"]["GatewayRole"]["Properties"]["Policies"]
    policies[:] = [p for p in policies if p["PolicyName"] != "McpCredentialUse"]
    configure_gateway(proposed["Resources"], release.state["journeyPlatform"])
    validate_resource_tags(proposed["Resources"])
    (release.evidence / "gateway-template-before.json").write_text(json.dumps(previous, indent=2))
    (release.evidence / "gateway-template-proposed.json").write_text(json.dumps(proposed, indent=2))
    if previous == proposed:
        release.receipt["gateway_verified"] = True
        release.save()
        print("Gateway credential permissions already deployed", flush=True)
        return
    cf.validate_template(TemplateBody=json.dumps(proposed))
    name = "mcp-credentials-" + digest(proposed)[:20]
    request = {"StackName": stack_id, "ChangeSetName": name, "ChangeSetType": "UPDATE",
               "TemplateBody": json.dumps(proposed), "Capabilities": ["CAPABILITY_IAM"],
               "Parameters": [{"ParameterKey": p["ParameterKey"], "UsePreviousValue": True} for p in stack["Parameters"]]}
    release.dispatch("gateway-plan-" + name, request, lambda: cf.create_change_set(**request, ClientToken=name))
    for _ in range(40):
        value = cf.describe_change_set(StackName=stack_id, ChangeSetName=name)
        if value["Status"] not in ("CREATE_PENDING", "CREATE_IN_PROGRESS"):
            break
        time.sleep(3)
    if value["Status"] != "CREATE_COMPLETE" or value.get("NextToken") or len(value["Changes"]) != 1:
        raise ValueError("Gateway change set unavailable or unexpected")
    change = value["Changes"][0]["ResourceChange"]
    if change["LogicalResourceId"] != "GatewayRole" or change["Action"] != "Modify" or change["Replacement"] != "False":
        raise ValueError("Gateway change must only extend its existing role")
    release.dispatch("gateway-execute-" + name, {"stack": stack_id, "change_set": name},
                     lambda: cf.execute_change_set(StackName=stack_id, ChangeSetName=name, ClientRequestToken=name))
    for _ in range(60):
        current = cf.describe_stacks(StackName=stack_id)["Stacks"][0]
        print("Existing Gateway role: " + current["StackStatus"], flush=True)
        if not current["StackStatus"].endswith("IN_PROGRESS"):
            break
        time.sleep(5)
    if current["StackStatus"] != "UPDATE_COMPLETE" or current["Outputs"] != stack["Outputs"]:
        raise ValueError("Existing Gateway role deployment not verified")
    release.receipt["gateway_verified"] = True
    release.save()


def enable(release):
    if not release.receipt.get("deployment_verified") or not release.receipt.get("gateway_verified"):
        raise ValueError("Deploy application and Gateway permissions before enabling credential setup")
    store = DynamoStore(release.state["app"]["outputs"]["StateTable"], release.target.session.resource("dynamodb"))
    with store.tx() as db:
        current = get(db, "mcp-onboarding")
        settings = get(db, "journey-platform")
        expected = release.state["journeyPlatform"]
        previous = copy.deepcopy(expected)
        previous["mcp_onboarding"].pop("credential_prefix", None)
        if current not in (previous["mcp_onboarding"], expected["mcp_onboarding"]) or settings not in (previous, expected):
            raise ValueError("Current settings changed; refusing overwrite")
        put(db, "mcp-onboarding", expected["mcp_onboarding"])
        put(db, "journey-platform", expected)
    print("Generic credential setup enabled", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "gateway", "enable"])
    target_arguments(parser)
    parser.add_argument("--evidence-name", default="mcp-generic-ui", type=evidence_name,
                        help="Use the same target-specific receipt directory for every MCP stage")
    args = parser.parse_args(argv)
    globals()[args.action](Release(args))


if __name__ == "__main__":
    main()
