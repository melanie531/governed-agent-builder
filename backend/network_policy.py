"""Strict AgentCore Runtime network configuration: PUBLIC or an explicit VPC binding, never a fallback."""
import copy


def validate_network(network):
    if network == {"networkMode": "PUBLIC"}:
        return {"networkMode": "PUBLIC"}
    if (not isinstance(network, dict) or set(network) != {"networkMode", "networkModeConfig"}
            or network["networkMode"] != "VPC" or not isinstance(network["networkModeConfig"], dict)
            or set(network["networkModeConfig"]) != {"subnets", "securityGroups"}
            or any(not isinstance(values, list) or not values
                   or any(not isinstance(v, str) or not v for v in values)
                   for values in network["networkModeConfig"].values())):
        raise ValueError("Runtime network configuration is invalid")
    return copy.deepcopy(network)


def networks_equivalent(a, b):
    try:
        a, b = validate_network(a), validate_network(b)
    except ValueError:
        return False
    if a["networkMode"] != b["networkMode"]:
        return False
    return a["networkMode"] == "PUBLIC" or all(
        sorted(a["networkModeConfig"][k]) == sorted(b["networkModeConfig"][k]) for k in ("subnets", "securityGroups"))
