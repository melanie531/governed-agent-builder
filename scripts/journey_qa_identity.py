"""Create a scoped, temporary QA identity for the authorized hosted journey test.

The email stays unverified. Existing protected QA enrollment binds its exact
Cognito subject to this app, workspace, and a 24-hour expiry. No email is sent.
"""
import argparse
import json
from pathlib import Path
import secrets
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.deployment_target import DeploymentTarget, target_arguments


def main():
    parser = argparse.ArgumentParser()
    target_arguments(parser)
    parser.add_argument("--role", choices=["business", "admin"], default="business")
    args = parser.parse_args()
    target = DeploymentTarget(args.expected_account, args.profile, args.region, args.state)
    state_key = "journeyAdminQA" if args.role == "admin" else "journeyQA"
    group = "studio-admin" if args.role == "admin" else "studio-research"
    if target.state.get(state_key):
        raise RuntimeError("QA identity already provisioned; do not reset it")
    outputs = target.state["app"]["outputs"]
    cognito, ssm = target.session.client("cognito-idp"), target.session.client("ssm")
    suffix = secrets.token_hex(6)
    username = f"journey-qa-{suffix}@example.com"
    prefix = "/governed-agent-builder/journey-qa/" + suffix
    password = secrets.token_urlsafe(40) + "aA7!"
    for name, value in (("username", username), ("password", password)):
        ssm.put_parameter(Name=prefix + "/" + name, Value=value, Type="SecureString",
                          Description="Temporary authorized Create Agent journey test", Overwrite=False,
                          Tags=[{"Key": "auto-delete", "Value": "no"},
                                {"Key": "project", "Value": "governed-agent-builder"}])
    created = cognito.admin_create_user(UserPoolId=outputs["UserPoolId"], Username=username,
        TemporaryPassword=password, MessageAction="SUPPRESS", UserAttributes=[{"Name": "email", "Value": username}])
    subject = next(item["Value"] for item in created["User"]["Attributes"] if item["Name"] == "sub")
    cognito.admin_set_user_password(UserPoolId=outputs["UserPoolId"], Username=username, Password=password, Permanent=True)
    del password
    cognito.admin_add_user_to_group(UserPoolId=outputs["UserPoolId"], Username=username, GroupName=group)
    now = int(time.time())
    enrollment = {"subject": subject, "issuer": f"https://cognito-idp.{args.region}.amazonaws.com/{outputs['UserPoolId']}",
                  "client": outputs["ClientId"], "origin": outputs["ApplicationOrigin"],
                  "group": group, "role": args.role, "workspace": "platform" if args.role == "admin" else "research", "enabled": True,
                  "enrolled_at": now, "expires": now + 86400,
                  "enrolled_by": target.session.client("sts").get_caller_identity()["Arn"],
                  "approval": "user-authorized-synthetic-create-agent-e2e"}
    path = target.path.parent / "journey-qa-enrollments.json"
    existing = json.loads(path.read_text()).get("enrollments", []) if path.exists() else []
    if any(any(row[key] != enrollment[key] for key in ("issuer", "client", "origin")) for row in existing):
        raise RuntimeError("Existing QA enrollment belongs to another target")
    path.write_text(json.dumps({"enrollments": [*existing, enrollment]}, indent=2))
    path.chmod(0o600)
    target.save(state_key, {"subject": subject, "parameterPrefix": prefix, "enrollment": str(path), "expires": now + 86400})
    print(f"Temporary {args.role} QA identity enrolled; email unverified, no invitation sent.", flush=True)


if __name__ == "__main__":
    main()
