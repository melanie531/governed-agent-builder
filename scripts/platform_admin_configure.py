"""Provision or adopt one project-owned AWS Agent Registry for this explicitly bound platform."""
import argparse
from pathlib import Path
import sys
import time
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from foundation_harness.config import digest
from scripts.deployment_target import DeploymentTarget, target_arguments

NAME = "governed-agent-builder"


def main():
    parser = argparse.ArgumentParser()
    target_arguments(parser)
    args = parser.parse_args()
    target = DeploymentTarget(args.expected_account, args.profile, args.region, args.state)
    control = target.session.client("agent-registry-control")
    saved = target.state.get("platformAdmin")
    if not saved or not saved.get("registry_arn"):
        # Adopt only this project's registry: exact name AND project tag; never another team's.
        existing = [row for row in control.list_registries().get("registries", []) if row.get("name") == NAME]
        if existing:
            arn = existing[0]["registryArn"]
            tags = control.list_tags_for_resource(resourceArn=arn)["tags"]
            if tags.get("project") != NAME:
                raise RuntimeError("A same-name registry exists without this project's tag; operator review required")
        else:
            try:
                response = control.create_registry(
                    name=NAME, description="Platform-reviewed Agent Studio resource metadata; manual approval only",
                    tags={"project": NAME},
                    clientToken=digest([target.binding, "platform-admin-registry-v2"]))
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "AccessDeniedException":
                    raise
                target.save("platformAdmin", {"enabled": True, "registry_status": "UNAVAILABLE",
                                             "registry_error": "AccessDeniedException"})
                print("AWS denied native Registry creation. Other administrator integrations can be enabled; Registry publication remains unavailable.", flush=True)
                return
            arn = response["registryArn"]
        saved = {"enabled": True, "registry_arn": arn, "registry_status": "READY"}
        target.save("platformAdmin", saved)
    for _ in range(30):
        current = control.get_registry(registryId=saved["registry_arn"])
        if current["name"] != NAME:
            raise RuntimeError("Registry ownership mismatch")
        if current["status"] == "READY":
            if (current.get("approvalConfiguration") or {}).get("autoApprovalRules"):
                raise RuntimeError("Auto-approval rules are prohibited; explicit review is required")
            print("Platform Registry ready:", saved["registry_arn"], flush=True)
            return
        if current["status"] not in ("CREATING", "UPDATING"):
            raise RuntimeError("Registry provisioning failed: " + current["status"])
        time.sleep(5)
    raise TimeoutError("Registry did not become ready")


if __name__ == "__main__":
    main()
