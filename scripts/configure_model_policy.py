"""Plan or apply the platform model policy (global or au) for one installed deployment target.

The default is a read-only plan. --apply updates the bound journeyPlatform and
the live journey-platform settings together. Existing agents and Runtimes are
not changed; they must be revised and redeployed to adopt a new policy.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.deployment_target import target_arguments


def run(target, policy, *, apply):
    from backend.model_policy import model_policy
    model_policy({"model_policy": policy})
    bound = target.state.get("journeyPlatform")
    if not bound:
        raise RuntimeError("Platform is not installed for this target; run journey_platform prepare first")
    result = {"current": model_policy(bound), "requested": policy, "applied": False,
              "would_write": ["deployment state journeyPlatform.model_policy", "live journey-platform settings model_policy"]}
    if apply:
        from backend.dynamo_store import DynamoStore
        from backend.foundation_runs import get, put
        updated = {**bound, "model_policy": policy}
        store = DynamoStore(target.state["app"]["outputs"]["StateTable"], target.session.resource("dynamodb"))
        with store.tx() as db:
            # An identical live update means an earlier apply stopped before saving local state.
            if get(db, "journey-platform") not in (bound, updated):
                raise RuntimeError("Live platform settings differ; reconcile the bound state first")
            put(db, "journey-platform", updated)
        target.save("journeyPlatform", updated)
        result["applied"] = True
    print(json.dumps(result, indent=2), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--policy", required=True, choices=["global", "au"])
    parser.add_argument("--apply", action="store_true", help="Write the policy; default prints the plan only")
    target_arguments(parser)
    args = parser.parse_args()
    from scripts.bootstrap_support import PlatformTarget
    run(PlatformTarget(args.expected_account, args.profile, args.region, args.state), args.policy, apply=args.apply)


if __name__ == "__main__":
    main()
