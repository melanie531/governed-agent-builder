"""Provision one IAM-authorized Registry for this explicitly bound platform."""
import argparse
from pathlib import Path
import sys
import time
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from foundation_harness.config import digest
from scripts.deployment_target import DeploymentTarget, target_arguments


def main():
    parser = argparse.ArgumentParser()
    target_arguments(parser)
    args = parser.parse_args()
    target = DeploymentTarget(args.expected_account, args.profile, args.region, args.state)
    control = target.session.client("bedrock-agentcore-control")
    saved = target.state.get("platformAdmin")
    if not saved or not saved.get("registry_arn"):
        try:
            response = control.create_registry(
                name="governed-agent-builder", description="Platform-reviewed Agent Studio resource metadata",
                authorizerType="AWS_IAM", approvalConfiguration={"autoApproval": False},
                clientToken=digest([target.binding, "platform-admin-registry-v1"]))
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "AccessDeniedException":
                raise
            target.save("platformAdmin", {"enabled": True, "registry_status": "UNAVAILABLE",
                                         "registry_error": "AccessDeniedException"})
            print("AWS denied native Registry creation. Other administrator integrations can be enabled; Registry publication remains unavailable.", flush=True)
            return
        saved = {"enabled": True, "registry_arn": response["registryArn"], "registry_status": "READY"}
        target.save("platformAdmin", saved)
    for _ in range(30):
        current = control.get_registry(registryId=saved["registry_arn"])
        if current["name"] != "governed-agent-builder" or current.get("authorizerType") != "AWS_IAM":
            raise RuntimeError("Registry ownership or authorizer mismatch")
        if current["status"] == "READY":
            if current.get("approvalConfiguration", {}).get("autoApproval") is not False:
                raise RuntimeError("Explicit Registry approval must be enabled")
            print("Platform Registry ready:", saved["registry_arn"], flush=True)
            return
        if current["status"] not in ("CREATING", "UPDATING"):
            raise RuntimeError("Registry provisioning failed: " + current["status"])
        time.sleep(5)
    raise TimeoutError("Registry did not become ready")


if __name__ == "__main__":
    main()
