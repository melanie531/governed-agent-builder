"""Plan or apply the AgentCore Runtime VPC network for one bound deployment target.

The default is a read-only plan. --apply records the validated network as the
installation choice (agentNetwork) and, for an installed platform, updates the
bound journeyPlatform and live journey-platform settings together. It never
writes a PUBLIC fallback and never changes existing Runtimes: each keeps the
network pinned at its creation, and apply lists them for manual migration.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.deployment_target import target_arguments


def ids(value):
    return [item.strip() for item in value.split(",")]


def described(call, key, field, names, label):
    from botocore.exceptions import ClientError
    try:
        found = call(**{field: names})[key]
    except ClientError as exc:
        raise RuntimeError(f"{label} {', '.join(names)} was not found in this account and region "
                           f"({exc.response['Error']['Code']}); check the IDs and the --region") from None
    return found


def gateway_service(ec2, region, subnets):
    from botocore.exceptions import ClientError
    service = f"com.amazonaws.{region}.bedrock-agentcore.gateway"
    try:
        details = ec2.describe_vpc_endpoint_services(ServiceNames=[service])["ServiceDetails"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "InvalidServiceName":
            raise
        details = []
    details = [d for d in details if d["ServiceName"] == service]
    if not details:
        raise RuntimeError(f"AgentCore Gateway PrivateLink service {service} is not available in region {region}; "
                           "choose a region where AgentCore Gateway supports VPC endpoints")
    supported = set(details[0].get("AvailabilityZones", []))
    unsupported = [f"{s['SubnetId']} ({s['AvailabilityZone']})" for s in subnets if s["AvailabilityZone"] not in supported]
    if unsupported:
        raise RuntimeError(f"subnet(s) {', '.join(unsupported)} are in Availability Zones {service} does not support "
                           f"(supported: {', '.join(sorted(supported))}); choose subnets in supported zones")
    return f"PrivateLink service {service} is available in Availability Zones {', '.join(sorted({s['AvailabilityZone'] for s in subnets}))}"


def plan(target, vpc_id, subnets, groups):
    from backend.network_policy import validate_network
    network = validate_network({"networkMode": "VPC", "networkModeConfig": {"subnets": subnets, "securityGroups": groups}})
    ec2 = target.session.client("ec2")
    vpcs = described(ec2.describe_vpcs, "Vpcs", "VpcIds", [vpc_id], "VPC")
    if [v["VpcId"] for v in vpcs] != [vpc_id] or vpcs[0].get("OwnerId", target.binding["account"]) != target.binding["account"]:
        raise RuntimeError(f"VPC {vpc_id} was not found in account {target.binding['account']}")
    validations = [f"VPC {vpc_id} exists in account {target.binding['account']} region {target.binding['region']}"]
    # Private DNS for the Gateway interface endpoint needs both; report, never modify the VPC.
    for attribute, flag in (("enableDnsSupport", "--enable-dns-support"), ("enableDnsHostnames", "--enable-dns-hostnames")):
        if not ec2.describe_vpc_attribute(VpcId=vpc_id, Attribute=attribute)[attribute[0].upper() + attribute[1:]]["Value"]:
            raise RuntimeError(f"VPC {vpc_id} has {attribute} disabled; the private AgentCore Gateway endpoint needs it. "
                               f"Enable it with: aws ec2 modify-vpc-attribute --vpc-id {vpc_id} {flag} '{{\"Value\":true}}'")
    validations.append(f"VPC {vpc_id} has enableDnsSupport and enableDnsHostnames enabled")
    found = described(ec2.describe_subnets, "Subnets", "SubnetIds", subnets, "subnets")
    outside = [s["SubnetId"] for s in found if s["VpcId"] != vpc_id]
    if outside or len(found) != len(set(subnets)):
        raise RuntimeError(f"subnet(s) {', '.join(outside) or 'requested'} not in VPC {vpc_id}")
    zones = {s["AvailabilityZone"] for s in found}
    if len(zones) < 2:
        raise RuntimeError("Runtime subnets must span at least two Availability Zones")
    validations.append(f"{len(found)} subnets belong to {vpc_id} across Availability Zones {', '.join(sorted(zones))}")
    validations.append(gateway_service(ec2, target.binding["region"], found))
    found = described(ec2.describe_security_groups, "SecurityGroups", "GroupIds", groups, "security groups")
    outside = [g["GroupId"] for g in found if g["VpcId"] != vpc_id]
    if outside or len(found) != len(set(groups)):
        raise RuntimeError(f"security group(s) {', '.join(outside) or 'requested'} not in VPC {vpc_id}")
    validations.append(f"{len(found)} security groups belong to {vpc_id}")
    tables = ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["RouteTables"]
    main = [t for t in tables if any(a.get("Main") for a in t.get("Associations", []))]
    for subnet in subnets:
        table = [t for t in tables if any(a.get("SubnetId") == subnet for a in t.get("Associations", []))] or main
        if any(r.get("DestinationCidrBlock") == "0.0.0.0/0" and r.get("GatewayId", "").startswith("igw-")
               for t in table for r in t.get("Routes", [])):
            raise RuntimeError(f"{subnet} is a public subnet (0.0.0.0/0 routes to an internet gateway); "
                               "use private subnets with NAT or VPC endpoints")
    validations.append("no subnet has a direct 0.0.0.0/0 internet gateway route")
    return network, validations


def existing_runtimes(db):
    """Report, never change, the network each uploaded MCP Runtime keeps after a platform switch."""
    runtimes = []
    for row in db.select("settings"):
        if row["key"].startswith("mcp-python:") and (state := json.loads(row["body"])).get("phase") != "DELETED":
            runtimes.append({"id": state["id"], "name": state.get("name"),
                "network": state.get("network") or {"networkMode": "PUBLIC"},
                "pinned": "receipt" if state.get("network") else "legacy-PUBLIC"})
    return {"runtimes": runtimes, "note": "existing Runtimes keep their pinned network; to migrate, retire and "
            "re-upload each one after apply (manual, not automated)"}


def run(target, vpc_id, subnets, groups, *, apply):
    network, validations = plan(target, vpc_id, subnets, groups)
    installed = bool(target.state.get("journeyPlatform"))
    writes = ["deployment state agentNetwork"] + (
        ["deployment state journeyPlatform.network", "live journey-platform settings network"] if installed else [])
    result = {"network": network, "validations": validations, "would_write": writes, "applied": False}
    if apply:
        if installed:
            from backend.dynamo_store import DynamoStore
            from backend.foundation_runs import get, put
            bound = target.state["journeyPlatform"]
            updated = {**bound, "network": network}
            store = DynamoStore(target.state["app"]["outputs"]["StateTable"], target.session.resource("dynamodb"))
            with store.tx() as db:
                # An identical live update means an earlier apply stopped before saving local state.
                if get(db, "journey-platform") not in (bound, updated):
                    raise RuntimeError("Live platform settings differ; reconcile the bound state first")
                put(db, "journey-platform", updated)
                result["existing_runtimes"] = existing_runtimes(db)
            target.save("journeyPlatform", updated)
        target.save("agentNetwork", network)
        result["applied"] = True
    print(json.dumps(result, indent=2), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vpc-id", required=True)
    parser.add_argument("--subnets", required=True, type=ids, help="Comma-separated private subnet IDs in at least two AZs")
    parser.add_argument("--security-groups", required=True, type=ids, help="Comma-separated Runtime security group IDs")
    parser.add_argument("--apply", action="store_true", help="Write the validated network; default prints the plan only")
    target_arguments(parser)
    args = parser.parse_args()
    from scripts.bootstrap_support import PlatformTarget
    target = PlatformTarget(args.expected_account, args.profile, args.region, args.state)
    run(target, args.vpc_id, args.subnets, args.security_groups, apply=args.apply)


if __name__ == "__main__":
    main()
