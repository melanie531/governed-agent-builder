"""Native cleanup operations restricted to server-derived agent resource names."""
import json
import re

from botocore.exceptions import ClientError


def missing(exc):
    return isinstance(exc, ClientError) and exc.response["Error"]["Code"] == "ResourceNotFoundException"


def find_runtimes(cloud, agent_id, workspace, plans):
    names = {plan["name"]: plan for plan in plans}
    found = {plan["binding"]["id"]: plan["binding"] for plan in plans if plan.get("binding")}
    token = None
    while True:
        result = cloud.control.list_agent_runtimes(**({"nextToken": token} if token else {}))
        for runtime in result.get("agentRuntimes", []):
            if runtime["agentRuntimeName"] in names:
                found[runtime["agentRuntimeId"]] = {"id": runtime["agentRuntimeId"], "arn": runtime["agentRuntimeArn"]}
        token = result.get("nextToken")
        if not token:
            break
    resources = []
    for binding in found.values():
        runtime_id = binding["id"]
        prefix = f"arn:aws:bedrock-agentcore:{cloud.settings['region']}:{cloud.settings['account']}:runtime/"
        if (binding["arn"] != prefix + runtime_id or not re.fullmatch(r"gab_journey_[a-f0-9]{24}-[A-Za-z0-9]+", runtime_id)
                or runtime_id.split("-")[0] not in names):
            raise ValueError("Cleanup resource does not match the saved agent namespace")
        try:
            native = cloud.control.get_agent_runtime(agentRuntimeId=runtime_id)
            tags = cloud.control.list_tags_for_resource(resourceArn=binding["arn"])["tags"]
            if (native["roleArn"] != cloud.settings["runtime_role"]
                    or tags.get("project") != "governed-agent-builder" or tags.get("journey") != "create-agent"
                    or tags.get("agent") != agent_id or tags.get("workspace") != workspace):
                raise ValueError("Runtime ownership changed; cleanup requires operator review")
        except ClientError as exc:
            if not missing(exc):
                raise
        resources.append({"id": runtime_id, "arn": binding["arn"]})
    return resources


def delete_runtime(cloud, binding):
    try:
        native = cloud.control.get_agent_runtime(agentRuntimeId=binding["id"])
    except ClientError as exc:
        if not missing(exc):
            raise
        # Native runtime groups contain the immutable Runtime ID.
        prefix = "/aws/bedrock-agentcore/runtimes/" + binding["id"]
        logs = cloud.session.client("logs")
        for page in logs.get_paginator("describe_log_groups").paginate(logGroupNamePrefix=prefix):
            for group in page["logGroups"]:
                name = group["logGroupName"]
                if name == prefix or name.startswith(prefix + "-") or name.startswith(prefix + "/"):
                    logs.delete_log_group(logGroupName=name)
        return True
    if native["status"] != "DELETING":
        cloud.control.delete_agent_runtime(agentRuntimeId=binding["id"])
    return False


def purge_objects(cloud, entry):
    prefix = entry["prefix"]
    if not (re.fullmatch(r"journey/manifests/[a-f0-9]{64}\.json", prefix) and entry["exact"]
            or re.fullmatch(r"journey/(evidence/[a-f0-9]{64}|evaluations/[a-f0-9]{32})/", prefix) and not entry["exact"]):
        raise ValueError("Cleanup prefix is not an agent artifact namespace")
    result = cloud.s3.list_object_versions(Bucket=cloud.settings["bucket"], Prefix=prefix, MaxKeys=100)
    objects = [{"Key": item["Key"], "VersionId": item["VersionId"]}
               for item in result.get("Versions", []) + result.get("DeleteMarkers", [])
               if not entry["exact"] or item["Key"] == prefix]
    if not objects:
        return True
    response = cloud.s3.delete_objects(Bucket=cloud.settings["bucket"], Delete={"Objects": objects, "Quiet": True})
    if response.get("Errors"):
        raise ValueError("Some agent artifacts could not be removed; retry cleanup")
    return False


def delete_streams(cloud, agent_id, versions):
    if not re.fullmatch(r"[a-f0-9]{32}", agent_id):
        raise ValueError("Invalid agent cleanup scope")
    logs = cloud.session.client("logs")
    for version in versions:
        try:
            logs.delete_log_stream(logGroupName=cloud.settings["log_group"],
                                   logStreamName=f"agent-{agent_id}-v{int(version)}")
        except ClientError as exc:
            if not missing(exc):
                raise
