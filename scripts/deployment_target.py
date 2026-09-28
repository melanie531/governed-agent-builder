"""Explicit target binding. Importing this module never creates an AWS session."""
import json
from pathlib import Path
import re

import boto3
from botocore.exceptions import ClientError

PROJECT = "governed-agent-builder"
PREFIX = PROJECT + "-serverless"


def target_arguments(parser):
    parser.add_argument("--expected-account", required=True)
    parser.add_argument("--profile", required=True, help="Operator-approved SDK profile; default must be explicit")
    parser.add_argument("--region", required=True)
    parser.add_argument("--state", required=True, type=Path, help="Fresh target-specific local JSON path")


class DeploymentTarget:
    def __init__(self, expected_account, profile, region, state, session_factory=None, *, reconciled_rollback_stack_id=None):
        if not re.fullmatch(r"[0-9]{12}", expected_account) or not profile.strip() or not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d", region):
            raise RuntimeError("Explicit expected account, approved profile and region required")
        self.path = Path(state).expanduser().resolve()
        if self.path.name in {"serverless-deployment.json", "governed-agent-builder-cloud-state.json"}:
            raise RuntimeError("Legacy state path prohibited; use a fresh target-specific path")
        self.binding = {"account": expected_account, "profile": profile, "region": region}
        self.reconciled_rollback_stack_id = reconciled_rollback_stack_id
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {"target": self.binding}
        if self.state.get("target") != self.binding:
            raise RuntimeError("State target mismatch or unbound legacy state")
        self.session = (session_factory or boto3.Session)(profile_name=profile, region_name=region)
        if self.session.client("sts").get_caller_identity()["Account"] != expected_account:
            raise RuntimeError("STS account mismatch; no writes permitted")
        self.cf = self.session.client("cloudformation")
        self.check_stacks()

    def check_stacks(self):
        """Read-only checks before a command's first AWS write, including S3 upload."""
        for suffix in ("artifacts", "app"):
            saved = self.state.get(suffix)
            try:
                stack = self.cf.describe_stacks(StackName=PREFIX + "-" + suffix)["Stacks"][0]
            except ClientError as exc:
                error = exc.response["Error"]
                if error["Code"] != "ValidationError" or "does not exist" not in error.get("Message", ""):
                    raise
                if saved:
                    raise RuntimeError("Recorded target stack missing; refusing stale artifacts") from None
                continue
            self.check_stack(stack)
            outputs = {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}
            if not saved or saved.get("stackId") != stack["StackId"] or saved.get("outputs") != outputs:
                raise RuntimeError("Live stack/state identity mismatch; no adoption or old artifacts allowed")
            reconciled = (stack["StackStatus"] == "UPDATE_ROLLBACK_COMPLETE"
                          and stack["StackId"] == self.reconciled_rollback_stack_id)
            if stack["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE") and not reconciled:
                raise RuntimeError("Target stack not stable; operator reconciliation required")
        if "app" in self.state and "artifacts" not in self.state:
            raise RuntimeError("App requires target-bound artifacts stack")
        if "app" in self.state:
            app = self.cf.describe_stacks(StackName=PREFIX + "-app")["Stacks"][0]
            parameters = {p["ParameterKey"]: p.get("ParameterValue") for p in app.get("Parameters", [])}
            if parameters.get("ArtifactBucket") != self.state["artifacts"]["outputs"].get("Bucket"):
                raise RuntimeError("App release bucket does not match target artifacts")

    def check_stack(self, stack):
        arn = stack["StackId"].split(":")
        if len(arn) < 6 or arn[2] != "cloudformation" or arn[3] != self.binding["region"] or arn[4] != self.binding["account"]:
            raise RuntimeError("Stack account/region mismatch")
        tags = {t["Key"]: t["Value"] for t in stack.get("Tags", [])}
        if tags.get("project") != PROJECT or tags.get("architecture") != "managed-serverless":
            raise RuntimeError("Unowned target stack")
        for output in stack.get("Outputs", []):
            value = output["OutputValue"]
            if value.startswith("arn:"):
                parts = value.split(":")
                if (parts[3] and parts[3] != self.binding["region"]) or (parts[4] and parts[4] != self.binding["account"]):
                    raise RuntimeError("Output ARN target mismatch")

    def save(self, key, value):
        self.state[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(mode=0o600, exist_ok=True)
        self.path.chmod(0o600)
        self.path.write_text(json.dumps(self.state, indent=2))
