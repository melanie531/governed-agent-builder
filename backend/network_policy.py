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
        a, b = validate_network(reported_network(a)), validate_network(reported_network(b))
    except ValueError:
        return False
    if a["networkMode"] != b["networkMode"]:
        return False
    return a["networkMode"] == "PUBLIC" or all(
        sorted(a["networkModeConfig"][k]) == sorted(b["networkModeConfig"][k]) for k in ("subnets", "securityGroups"))


def reported_network(network):
    """Drop only the known-harmless service-reported VPC default (requireServiceS3Endpoint=False).

    Requests never send it and the service reports False by default. Any other
    service-added field or value (including requireServiceS3Endpoint=True) is
    kept, so validate_network rejects the shape and equivalence fails closed
    into the existing operator-review errors.
    """
    if (not isinstance(network, dict) or network.get("networkMode") != "VPC"
            or not isinstance(network.get("networkModeConfig"), dict)):
        return network
    config = {k: v for k, v in network["networkModeConfig"].items()
              if k != "requireServiceS3Endpoint" or v is not False}
    return {**network, "networkModeConfig": config}
