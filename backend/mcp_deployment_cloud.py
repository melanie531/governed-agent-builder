"""Delete only receipt-bound MCP resources; shared infrastructure is never owned here."""
import base64
import re

from botocore.exceptions import ClientError

from .mcp_python_cloud import artifact_bucket


def missing(call):
    try:
        return call()
    except ClientError as error:
        if error.response["Error"]["Code"] in ("ResourceNotFoundException", "NoSuchKey", "NoSuchVersion", "NotFound", "404"):
            return None
        raise


def runtime(cloud, state, config):
    return missing(lambda: cloud.runtime(state, config, retiring=True))


def identity_arn(cloud, name):
    return (f"arn:aws:bedrock-agentcore:{cloud.settings['region']}:{cloud.settings['account']}:"
            "workload-identity-directory/default/workload-identity/" + name)


def object_request(cloud, resource, state, config):
    key = resource["key"]
    if (resource["bucket"] != artifact_bucket(config)
            or not (key == cloud.object_key(state)
                    or re.fullmatch(re.escape("mcp/python/uploads/" + state["id"] + "/part-") + r"\d{4}", key))
            or not resource["version_id"] or resource["version_id"] == "null"):
        raise ValueError("MCP deletion object ownership changed")
    return {"Bucket": resource["bucket"], "Key": key, "VersionId": resource["version_id"],
            "ExpectedBucketOwner": cloud.settings["account"]}


def object_head(cloud, resource, state, config):
    request = object_request(cloud, resource, state, config)
    try:
        head = missing(lambda: cloud.s3.head_object(**request, ChecksumMode="ENABLED"))
    except ClientError as error:
        if error.response["Error"]["Code"] not in ("403", "AccessDenied"):
            raise
        # A version-specific HEAD can hide absence behind 403. Inspect only this
        # exact key; never convert a denied read of a present version to absence.
        page = cloud.s3.list_object_versions(Bucket=request["Bucket"], Prefix=request["Key"],
            MaxKeys=100, ExpectedBucketOwner=cloud.settings["account"])
        if page.get("IsTruncated"):
            raise ValueError("MCP object-version inventory is incomplete") from None
        if any(v["Key"] == request["Key"] and v["VersionId"] == request["VersionId"]
               for v in page.get("Versions", []) + page.get("DeleteMarkers", [])):
            raise
        return None
    if not head:
        return None
    if (head.get("VersionId") != resource["version_id"]
            or head.get("ChecksumSHA256") != base64.b64encode(bytes.fromhex(resource["digest"])).decode()
            or head.get("ServerSideEncryption") != "AES256"
            or head.get("Metadata") != resource["metadata"]
            or head.get("ContentLength") != resource["size"] or head.get("ETag") != resource["etag"]):
        raise ValueError("MCP deletion object version changed")
    tags = cloud.s3.get_object_tagging(**request)["TagSet"]
    if {t["Key"]: t["Value"] for t in tags}.get("auto-delete") != "no":
        raise ValueError("MCP deletion object retention tag changed")
    return head


def inventory(cloud, state, config):
    """Freeze owned resource identities before the first deletion is dispatched."""
    native = runtime(cloud, state, config)
    if native and (native.get("pending") or native.get("deleting")):
        return None
    package = cloud.read("package", state, config)
    if state.get("artifact") and not package:
        raise ValueError("The retained MCP artifact is missing; inspect its pinned version before deletion")
    if package and state.get("artifact") and package["artifact"] != state["artifact"]:
        raise ValueError("MCP deployment artifact version changed")
    result = []
    if native:
        result.append({"kind": "runtime", **native})
    rid = (native or {}).get("runtime_id") or state.get("runtime_id")
    if rid:
        if not re.fullmatch(re.escape(state["runtime_name"]) + r"-[A-Za-z0-9]+", rid):
            raise ValueError("MCP workload identity ownership changed")
        value = missing(lambda: cloud.control.get_workload_identity(name=rid))
        if value:
            arn = identity_arn(cloud, rid)
            tags = cloud.control.list_tags_for_resource(resourceArn=arn)["tags"]
            required = cloud.tags(state, config)
            if (value["name"] != rid or value["workloadIdentityArn"] != arn
                    or value.get("allowedResourceOauth2ReturnUrls")
                    or any(k in tags and tags[k] != v for k, v in required.items())
                    or not native and any(tags.get(k) != v for k, v in required.items())):
                raise ValueError("MCP workload identity binding changed")
            result.append({"kind": "identity", "name": rid, "arn": arn,
                           "created": value["createdTime"].isoformat(), "tags": tags})
    objects = []
    if package:
        objects.append(package["artifact"])
    if state.get("upload_type") == "package":
        from .mcp_package import PackageCloud
        source = PackageCloud(cloud.settings, cloud.session)
        if state.get("source_parts"):
            for index, part in enumerate(state["source_parts"]):
                current = source.read_part(state, index, part, {"bucket": artifact_bucket(config)})
                if current != part:
                    raise ValueError("MCP uploaded part version changed")
                objects.append({**part, "bucket": artifact_bucket(config)})
        else:
            for index, part in sorted(state.get("parts", {}).items(), key=lambda item: int(item[0])):
                current = source.read_part(state, int(index), part, {"bucket": artifact_bucket(config)})
                if current:
                    objects.append({**current, "bucket": artifact_bucket(config)})
    for item in objects:
        head = cloud.s3.head_object(Bucket=item["bucket"], Key=item["key"], VersionId=item["version_id"],
            ExpectedBucketOwner=cloud.settings["account"], ChecksumMode="ENABLED")
        resource = {"kind": "object", **{k: item[k] for k in ("bucket", "key", "version_id", "digest")},
                    "metadata": head["Metadata"], "size": head["ContentLength"], "etag": head["ETag"]}
        object_head(cloud, resource, state, config)
        result.append(resource)
    return result


def read(cloud, resource, state, config):
    if resource["kind"] == "runtime":
        native = runtime(cloud, state, config)
        if native and any(native.get(k) != resource[k] for k in ("runtime_id", "runtime_arn", "runtime_version")):
            raise ValueError("MCP deletion Runtime identity changed")
        return {"absent": native is None, "pending": bool(native and native.get("deleting"))}
    if resource["kind"] == "identity":
        if (resource["name"] != state.get("runtime_id", resource["name"])
                or not re.fullmatch(re.escape(state["runtime_name"]) + r"-[A-Za-z0-9]+", resource["name"])
                or resource["arn"] != identity_arn(cloud, resource["name"])):
            raise ValueError("MCP deletion identity scope changed")
        value = missing(lambda: cloud.control.get_workload_identity(name=resource["name"]))
        if value and (value["workloadIdentityArn"] != resource["arn"]
                      or value["name"] != resource["name"] or value.get("allowedResourceOauth2ReturnUrls")
                      or value["createdTime"].isoformat() != resource["created"]
                      or cloud.control.list_tags_for_resource(resourceArn=resource["arn"])["tags"] != resource["tags"]):
            raise ValueError("MCP deletion workload identity changed")
        return {"absent": value is None, "pending": False}
    if resource["kind"] == "object":
        return {"absent": object_head(cloud, resource, state, config) is None, "pending": False}
    raise ValueError("Unsupported MCP deletion resource")


def write(cloud, resource, state, config):
    current = read(cloud, resource, state, config)
    if current["absent"] or current["pending"]:
        return
    if resource["kind"] == "runtime":
        return cloud.control.delete_agent_runtime(agentRuntimeId=resource["runtime_id"],
                                                  clientToken="delete-python-" + state["id"])
    if resource["kind"] == "identity":
        return cloud.control.delete_workload_identity(name=resource["name"])
    if resource["kind"] == "object":
        # The immutable VersionId pins the content verified above. S3 rejects
        # conditional delete headers when an explicit version is supplied.
        return cloud.s3.delete_object(**object_request(cloud, resource, state, config))
    raise ValueError("Unsupported MCP deletion resource")
