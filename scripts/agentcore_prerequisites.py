"""Install missing AWS-managed Runtime prerequisites without widening worker IAM."""
import argparse
import json

from botocore.exceptions import ClientError

from infra.journey import RUNTIME_SERVICE_LINKED_ROLES, runtime_prerequisites_template
from scripts.bootstrap_support import Journal, PlatformTarget
from scripts.deployment_target import target_arguments
from scripts.journey_platform import wait

STACK = "governed-agent-builder-agentcore-runtime-prerequisites"
TAGS = {"auto-delete": "no", "project": "governed-agent-builder"}


def install(target):
    iam, cf = target.session.client("iam"), target.cf
    operation = target.state.get("platformOperations", {}).get("runtime-prerequisites")
    if operation:
        request = operation["request"]
        body = json.loads(request["TemplateBody"])
        names = list(body["Resources"])
    else:
        names = []
        for name, service in RUNTIME_SERVICE_LINKED_ROLES.items():
            try:
                role = iam.get_role(RoleName=name)["Role"]
            except iam.exceptions.NoSuchEntityException:
                names.append(name)
            else:
                if role["Arn"] != (
                    f"arn:aws:iam::{target.binding['account']}:role/aws-service-role/{service}/{name}"
                ):
                    raise ValueError("Existing service-linked role identity differs")
        if not names:
            print("Runtime service-linked prerequisites already exist; preserved.", flush=True)
            return
        body = runtime_prerequisites_template(names)
        cf.validate_template(TemplateBody=json.dumps(body))
        try:
            cf.describe_stacks(StackName=STACK)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ValidationError" or "does not exist" not in exc.response["Error"]["Message"]:
                raise
        else:
            raise ValueError("Refusing to adopt an unrecorded prerequisites stack")
        request = {
            "StackName": STACK, "TemplateBody": json.dumps(body),
            "Capabilities": ["CAPABILITY_NAMED_IAM"],
            "Tags": [{"Key": k, "Value": v} for k, v in TAGS.items()],
        }

    def read(record):
        try:
            current = cf.describe_stacks(StackName=STACK)["Stacks"][0]
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ValidationError" and "does not exist" in exc.response["Error"]["Message"]:
                return None
            raise
        if (current["StackId"].split(":")[3:5] != [target.binding["region"], target.binding["account"]]
                or any({t["Key"]: t["Value"] for t in current["Tags"]}.get(k) != v for k, v in TAGS.items())):
            raise ValueError("Prerequisites stack ownership differs")
        actual = cf.get_template(StackName=STACK)["TemplateBody"]
        actual = json.loads(actual) if isinstance(actual, str) else actual
        if actual != body:
            raise ValueError("Prerequisites stack template differs")
        return {"stack_id": current["StackId"]}

    def write(token):
        return {"stack_id": cf.create_stack(**request, ClientRequestToken=token)["StackId"]}

    result = Journal(target).run("runtime-prerequisites", request, write, read)
    current = wait(lambda: cf.describe_stacks(StackName=result["stack_id"])["Stacks"][0])
    if read(None) != result:
        raise ValueError("Prerequisites stack is not verified")
    roles = {}
    for name in names:
        role = iam.get_role(RoleName=name)["Role"]
        expected = (f"arn:aws:iam::{target.binding['account']}:role/aws-service-role/"
                    f"{RUNTIME_SERVICE_LINKED_ROLES[name]}/{name}")
        if role["Arn"] != expected:
            raise ValueError("Created service-linked role differs")
        # IAM service-linked roles may reject tagging. Record the explicit AWS
        # limitation while retaining and tagging the owning CloudFormation stack.
        try:
            iam.tag_role(RoleName=name, Tags=[{"Key": k, "Value": v} for k, v in TAGS.items()])
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            message = exc.response["Error"].get("Message", "")
            if code not in {"UnmodifiableEntity", "InvalidInput"} or "service" not in message.lower():
                raise
            roles[name] = {"arn": expected, "tagging_supported": False, "aws_reason": message}
        else:
            tags = {t["Key"]: t["Value"] for t in iam.list_role_tags(RoleName=name)["Tags"]}
            if any(tags.get(k) != v for k, v in TAGS.items()):
                raise ValueError("Created service-linked role tags were not verified")
            roles[name] = {"arn": expected, "tagging_supported": True, "tags": tags}
    target.save("agentcoreRuntimePrerequisites", {
        **result, "status": current["StackStatus"], "roles": roles})
    print("Runtime account prerequisites verified; application role permissions preserved.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    target_arguments(parser)
    args = parser.parse_args()
    install(PlatformTarget(args.expected_account, args.profile, args.region, args.state))
