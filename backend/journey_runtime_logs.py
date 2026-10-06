"""Reproducible log-group configuration for an owned Journey Runtime."""
import re

from botocore.config import Config
from botocore.exceptions import ClientError


def provision(cloud, binding, *, runtime_prefix="gab_journey_", required_tags=None):
    account, region = cloud.settings["account"], cloud.settings["region"]
    runtime_id = binding["id"]
    expected = f"arn:aws:bedrock-agentcore:{region}:{account}:runtime/{runtime_id}"
    if not re.fullmatch(re.escape(runtime_prefix) + r"[A-Za-z0-9_-]+", runtime_id) or binding["arn"] != expected:
        raise ValueError("Runtime log binding does not match this deployment")
    tags = cloud.control.list_tags_for_resource(resourceArn=expected)["tags"]
    required = required_tags or {"project": "governed-agent-builder", "journey": "create-agent", "auto-delete": "no"}
    if any(tags.get(key) != value for key, value in required.items()):
        raise ValueError("Runtime owner or retention tags do not match")
    tags = {key: tags[key] for key in (*required, "agent", "workspace") if key in tags}
    name = "/aws/bedrock-agentcore/runtimes/" + runtime_id + "-DEFAULT"
    arn = f"arn:aws:logs:{region}:{account}:log-group:{name}"
    logs = cloud.session.client("logs", config=Config(retries={"total_max_attempts": 1},
                                                     connect_timeout=5, read_timeout=15))

    def read_group():
        response = logs.describe_log_groups(logGroupNamePrefix=name)
        group = next((entry for entry in response["logGroups"] if entry["logGroupName"] == name), None)
        if group is None and response.get("nextToken"):
            raise ValueError("Runtime log inventory is incomplete")
        if group is not None and group["arn"].removesuffix(":*") != arn:
            raise ValueError("Runtime log binding changed")
        return group

    group = read_group()
    if group is None:
        try:
            logs.create_log_group(logGroupName=name, tags=tags)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ResourceAlreadyExistsException":
                raise
            # AgentCore may create its log group between the read and create.
            group = read_group()
            if group is None:
                raise ValueError("Runtime log creation could not be reconciled") from exc
    if group is not None:
        current = logs.list_tags_for_resource(resourceArn=arn)["tags"]
        if any(key in current and current[key] != tags.get(key)
               for key in (*required, "agent", "workspace") if key != "auto-delete"):
            raise ValueError("Runtime log group has a conflicting owner")
        if any(current.get(key) != value for key, value in tags.items()):
            logs.tag_resource(resourceArn=arn, tags=tags)
    if group is None or group.get("retentionInDays") != 14:
        logs.put_retention_policy(logGroupName=name, retentionInDays=14)
    return {"logGroupName": name, "retentionInDays": 14}
