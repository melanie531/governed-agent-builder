"""Read-only audit checks that agent Runtimes use the approved network configuration."""
from backend.network_policy import networks_equivalent


def approved_network(state):
    """The explicit configure_agent_network choice; legacy states approve their installed network."""
    return state.get("agentNetwork") or state["journeyPlatform"].get("network") or {"networkMode": "PUBLIC"}


def business_agent_checks(state):
    return {"bound_network_approved": networks_equivalent(state["journeyPlatform"].get("network"), approved_network(state))}


def runtime_network_checks(control, server, approved):
    if not server.get("runtime_id") or server.get("deletion"):
        return {}
    native = control.get_agent_runtime(agentRuntimeId=server["runtime_id"],
                                       agentRuntimeVersion=server.get("runtime_version", "1"))
    return {"runtime_network_approved": networks_equivalent(native.get("networkConfiguration"), approved)}
