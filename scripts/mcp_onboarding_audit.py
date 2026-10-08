"""Read-only security/tag checks for an explicitly bound MCP deployment."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import boto3
from botocore.exceptions import ClientError

from infra.resource_tags import TAG_PROPERTIES


def reference_checks(request, connection, settings, auth, oauth_cloud):
    if request["phase"] == "DELETED":
        return {"metadata_absent": connection is None}
    config = settings["mcp_onboarding"]
    prefix = config["credential_prefix"]
    if request["auth_type"] == "GATEWAY_IAM_ROLE":
        from backend.mcp_iam import validate_connection
        validate_connection(connection, settings, prefix)
        checks = {"exact_runtime_binding": request["endpoint"] in connection["allowed_endpoints"]}
    else:
        from backend.mcp_gateway_oauth import validate_connection
        user = validate_connection(connection, config, settings)
        current = oauth_cloud.gateway_provider(user["provider_name"], prefix, auth, user["scopes"])
        checks = {"provider_binding_and_tags": current == user}
    return {**checks, "deployment_binding": request["deployment_prefix"] == prefix}


def python_resource_checks(cloud, server, config):
    """Verify resource safety without treating historical application failures as READY."""
    from backend.mcp_package import MAX_ZIP, PART_BYTES, PackageCloud
    from backend.mcp_python_cloud import artifact_bucket
    if server.get("deletion"):
        resources = server["deletion"].get("resources")
        checks = {"deletion_complete": server["phase"] == "DELETED", "inventory_frozen": resources is not None}
        for index, resource in enumerate(resources or []):
            checks["owned_resource_absent_" + str(index)] = cloud.retirement_read(resource, server, config)["absent"]
        return checks
    artifact = cloud.read("package", server, config)
    checks = {}
    if server.get("upload_type") == "package":
        parts = server.get("source_parts", [])
        parts_valid = (0 < server["size"] <= MAX_ZIP and bool(parts)
                       and len(parts) == (server["size"] + PART_BYTES - 1) // PART_BYTES)
        parts_tagged = bool(parts)
        for index, part in enumerate(parts):
            request = {"Bucket": artifact_bucket(config), "Key": part["key"],
                       "VersionId": part["version_id"], "ExpectedBucketOwner": cloud.settings["account"]}
            head = cloud.s3.head_object(**request, ChecksumMode="ENABLED")
            tags = {t["Key"]: t["Value"] for t in cloud.s3.get_object_tagging(**request)["TagSet"]}
            parts_valid = parts_valid and (
                part["key"] == PackageCloud.part_key(server["id"], index)
                and part["version_id"] != "null" and head.get("VersionId") == part["version_id"]
                and part["size"] == min(PART_BYTES, server["size"] - index * PART_BYTES)
                and head.get("ContentLength") == part["size"]
                and head.get("ServerSideEncryption") == "AES256"
                and head.get("ChecksumSHA256") == base64.b64encode(bytes.fromhex(part["digest"])).decode()
                and head.get("Metadata") == {"upload-id": server["id"],
                    "archive-digest": server["source_digest"], "sha256": part["digest"]})
            parts_tagged = parts_tagged and tags.get("auto-delete") == "no"
        checks.update(immutable_encrypted_upload_parts=parts_valid, upload_parts_retention_tag=parts_tagged)
    runtime = cloud.read("runtime", server, config)
    if artifact is None:
        return {**checks,
            "rejected_before_resource_creation": (
                server.get("upload_type") == "package" and server["stage"] == "package"
                and server["phase"] == "FAILED" and server.get("failure_code") == "INVALID_PACKAGE"
                and not server.get("artifact") and not server.get("runtime_id")
                and not server.get("operations", {}).get("runtime")),
            "rejected_package_has_no_runtime_artifact": artifact is None,
            "rejected_package_has_no_runtime": runtime is None}
    logs = cloud.read("logs", server, config) if runtime and not runtime.get("pending") else None
    recorded = server["artifact"]
    tags = {t["Key"]: t["Value"] for t in cloud.s3.get_object_tagging(
        Bucket=recorded["bucket"], Key=recorded["key"], VersionId=recorded["version_id"],
        ExpectedBucketOwner=cloud.settings["account"])["TagSet"]}
    return {**checks,
        "immutable_encrypted_package": artifact.get("artifact") == recorded and (
            server.get("connection_mode") != "PACKAGE"
            or artifact.get("bearer_validation_tool") == server.get("bearer_validation_tool")),
        "package_retention_tag": tags.get("auto-delete") == "no",
        "owned_private_runtime": bool(runtime) and not runtime.get("pending"),
        "generated_resources_and_logs_retained": bool(logs),
        "resource_stages_complete": server["stage"] == "schema" and (
            server["phase"] == "READY" or server["phase"] == "NEEDS_RECONCILIATION"
            and bool(server.get("failure_code")))}


def deployment_state(state, existing):
    """QA users are optional operator enrollments, not installation prerequisites."""
    if existing:
        state = {**state, "prefix": "governed-agent-builder-serverless",
                 "journey": {**state["journeyStack"], "stackId": state["journeyStack"]["id"]},
                 "gateway": {"gatewayId": state["journeyGateway"]["id"], "gatewayArn": state["journeyGateway"]["arn"]},
                 "gateway_identity_tagged": state["journeyGatewayIdentity"]["arn"],
                 "registry": state["mcpOnboardingRegistry"], "release": {"sha256": state["releaseSha256"]},
                 **({"qa-admin": state["journeyAdminQA"]} if state.get("journeyAdminQA") else {}),
                 **({"qa-business": state["journeyQA"]} if state.get("journeyQA") else {})}
    return state


def verified_scoped_worker_release(receipt_path, state, function_name):
    """Bind a Worker-only audit exception to the reviewed one-member ZIP patch."""
    receipt = json.loads(receipt_path.read_text())
    expected_base = state.get("releaseSha256", state.get("release", {}).get("sha256"))
    if (receipt.get("account") != state["target"]["account"]
            or receipt.get("region") != state["target"]["region"]
            or receipt.get("stack_id") != state["app"]["stackId"]
            or receipt.get("function") != function_name
            or receipt.get("base_zip_sha256") != expected_base
            or receipt.get("changed_zip_members") != ["backend/journey_cloud.py"]):
        raise ValueError("Scoped Worker release receipt is outside the bound target")
    base = receipt_path.with_name("base.zip")
    patch = receipt_path.with_name("worker-patched.zip")
    for path, expected in ((base, expected_base), (patch, receipt.get("patch_sha256"))):
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError("Scoped Worker package digest differs from the receipt")
    with ZipFile(base) as original, ZipFile(patch) as updated:
        names = original.namelist()
        if (names != updated.namelist() or len(names) != len(set(names))
                or names.count("backend/journey_cloud.py") != 1
                or original.testzip() or updated.testzip()
                or [name for name in names if original.read(name) != updated.read(name)]
                != receipt["changed_zip_members"]):
            raise ValueError("Scoped Worker ZIP changes more than the reviewed module")
    return receipt["patch_sha256"]


def verified_scoped_deploy_only_release(receipt_path, state, logical, function_name):
    """Verify the two one-member Lambda patches on top of their distinct live bases."""
    receipt = json.loads(receipt_path.read_text())
    original_sha = state.get("releaseSha256", state.get("release", {}).get("sha256"))
    if (receipt.get("account") != state["target"]["account"]
            or receipt.get("region") != state["target"]["region"]
            or receipt.get("stack_id") != state["app"]["stackId"]
            or receipt.get("base_zip_sha256") != original_sha
            or set(receipt.get("functions", {})) != {"Business", "Worker"}
            or logical not in ("Business", "Worker")):
        raise ValueError("Scoped deploy-only release receipt is outside the bound target")
    item = receipt["functions"][logical]
    if item.get("function") != function_name or item.get("changed_zip_members") != ["backend/journey.py"]:
        raise ValueError("Scoped deploy-only release changed an unapproved function or module")
    base_name = "base.zip" if logical == "Business" else "worker-patched.zip"
    patch_name = logical.lower() + "-deploy-only.zip"
    expected_base = (original_sha if logical == "Business" else
                     verified_scoped_worker_release(receipt_path.with_name("release-intent.json"), state, function_name))
    if item.get("base_sha256") != expected_base:
        raise ValueError("Scoped deploy-only release base differs from the live function")
    base, patch = receipt_path.with_name(base_name), receipt_path.with_name(patch_name)
    for path, expected in ((base, expected_base), (patch, item.get("patch_sha256"))):
        if not isinstance(expected, str) or len(expected) != 64:
            raise ValueError("Scoped deploy-only release has no pinned package digest")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError("Scoped deploy-only release package digest differs from the receipt")
    with ZipFile(base) as original, ZipFile(patch) as updated:
        names = original.namelist()
        if (names != updated.namelist() or len(names) != len(set(names))
                or names.count("backend/journey.py") != 1
                or original.testzip() or updated.testzip()
                or [name for name in names if original.read(name) != updated.read(name)]
                != item["changed_zip_members"]):
            raise ValueError("Scoped deploy-only release changed more than the reviewed module")
    return item["patch_sha256"]


def verified_scoped_consent_query_release(receipt_path, state, logical, function_name):
    """Verify the current three-member patch against the already audited live predecessor."""
    from scripts.scoped_consent_query_release import CHANGED, package_changes

    receipt = json.loads(receipt_path.read_text())
    if (receipt.get("target") != state["target"]
            or receipt.get("stack_id") != state["app"]["stackId"]
            or set(receipt.get("functions", {})) != {"Business", "Worker"}
            or set(receipt.get("base_paths", {})) != {"Business", "Worker"}
            or logical not in ("Business", "Worker")):
        raise ValueError("Scoped consent-query release is outside the bound target")
    item = receipt["functions"][logical]
    if (item.get("function") != function_name
            or item.get("changed_zip_members") != CHANGED
            or receipt.get("operations", {}).get(logical, {}).get("phase") != "VERIFIED"):
        raise ValueError("Scoped consent-query release lacks function or completion proof")
    previous_sha = verified_scoped_deploy_only_release(
        Path(receipt["previous_receipt"]), state, logical, function_name)
    if item.get("base_sha256") != previous_sha:
        raise ValueError("Scoped consent-query release predecessor differs")
    if package_changes(Path(receipt["base_paths"][logical]), Path(receipt["package_path"]),
                       previous_sha, item["patch_sha256"]) != CHANGED:
        raise ValueError("Scoped consent-query release has unexpected ZIP changes")
    return item["patch_sha256"]


def verified_scoped_connected_popup_release(receipt_path, state, logical, function_name):
    """Verify the two-member popup patch against the audited consent-query release."""
    from scripts.scoped_consent_query_release import CONNECTED_POPUP_CHANGED, package_changes

    receipt = json.loads(receipt_path.read_text())
    if (receipt.get("target") != state["target"]
            or receipt.get("stack_id") != state["app"]["stackId"]
            or set(receipt.get("functions", {})) != {"Business", "Worker"}
            or set(receipt.get("base_paths", {})) != {"Business", "Worker"}
            or logical not in ("Business", "Worker")):
        raise ValueError("Scoped connected-popup release is outside the bound target")
    item = receipt["functions"][logical]
    upload = receipt.get("upload", {})
    if (item.get("function") != function_name
            or item.get("changed_zip_members") != CONNECTED_POPUP_CHANGED
            or receipt.get("operations", {}).get(logical, {}).get("phase") != "VERIFIED"
            or upload.get("digest") != item.get("patch_sha256")
            or upload.get("key") != "journey/connected-popup/" + item.get("patch_sha256", "") + ".zip"):
        raise ValueError("Scoped connected-popup release lacks function or completion proof")
    previous_sha = verified_scoped_consent_query_release(
        Path(receipt["previous_receipt"]), state, logical, function_name)
    if item.get("base_sha256") != previous_sha:
        raise ValueError("Scoped connected-popup release predecessor differs")
    if package_changes(Path(receipt["base_paths"][logical]), Path(receipt["package_path"]),
                       previous_sha, item["patch_sha256"], CONNECTED_POPUP_CHANGED) != CONNECTED_POPUP_CHANGED:
        raise ValueError("Scoped connected-popup release has unexpected ZIP changes")
    return item["patch_sha256"]


def verified_scoped_reauth_release(receipt_path, state, logical, function_name):
    """Verify the three-member reauth patch against the audited popup release."""
    from scripts.scoped_consent_query_release import REAUTH_CHANGED, package_changes

    receipt = json.loads(receipt_path.read_text())
    if (receipt.get("target") != state["target"]
            or receipt.get("stack_id") != state["app"]["stackId"]
            or set(receipt.get("functions", {})) != {"Business", "Worker"}
            or set(receipt.get("base_paths", {})) != {"Business", "Worker"}
            or logical not in ("Business", "Worker")):
        raise ValueError("Scoped reauth release is outside the bound target")
    item = receipt["functions"][logical]
    upload = receipt.get("upload", {})
    if (item.get("function") != function_name
            or item.get("changed_zip_members") != REAUTH_CHANGED
            or receipt.get("operations", {}).get(logical, {}).get("phase") != "VERIFIED"
            or upload.get("digest") != item.get("patch_sha256")
            or upload.get("key") != "journey/reauth/" + item.get("patch_sha256", "") + ".zip"):
        raise ValueError("Scoped reauth release lacks function or completion proof")
    previous_sha = verified_scoped_connected_popup_release(
        Path(receipt["previous_receipt"]), state, logical, function_name)
    if item.get("base_sha256") != previous_sha:
        raise ValueError("Scoped reauth release predecessor differs")
    if package_changes(Path(receipt["base_paths"][logical]), Path(receipt["package_path"]),
                       previous_sha, item["patch_sha256"], REAUTH_CHANGED) != REAUTH_CHANGED:
        raise ValueError("Scoped reauth release has unexpected ZIP changes")
    return item["patch_sha256"]


def verified_scoped_reauth_idle_release(receipt_path, state, logical, function_name):
    """Verify the cached-page expiry patch against the audited reauth release."""
    from scripts.scoped_consent_query_release import REAUTH_IDLE_CHANGED, package_changes

    receipt = json.loads(receipt_path.read_text())
    if (receipt.get("target") != state["target"]
            or receipt.get("stack_id") != state["app"]["stackId"]
            or set(receipt.get("functions", {})) != {"Business", "Worker"}
            or set(receipt.get("base_paths", {})) != {"Business", "Worker"}
            or logical not in ("Business", "Worker")):
        raise ValueError("Scoped reauth-idle release is outside the bound target")
    item = receipt["functions"][logical]
    upload = receipt.get("upload", {})
    if (item.get("function") != function_name
            or item.get("changed_zip_members") != REAUTH_IDLE_CHANGED
            or receipt.get("operations", {}).get(logical, {}).get("phase") != "VERIFIED"
            or upload.get("digest") != item.get("patch_sha256")
            or upload.get("key") != "journey/reauth-idle/" + item.get("patch_sha256", "") + ".zip"):
        raise ValueError("Scoped reauth-idle release lacks function or completion proof")
    previous_sha = verified_scoped_reauth_release(
        Path(receipt["previous_receipt"]), state, logical, function_name)
    if item.get("base_sha256") != previous_sha:
        raise ValueError("Scoped reauth-idle release predecessor differs")
    if package_changes(Path(receipt["base_paths"][logical]), Path(receipt["package_path"]),
                       previous_sha, item["patch_sha256"], REAUTH_IDLE_CHANGED) != REAUTH_IDLE_CHANGED:
        raise ValueError("Scoped reauth-idle release has unexpected ZIP changes")
    return item["patch_sha256"]


def verified_scoped_native_gateway_release(receipt_path, state, logical, function_name):
    """Verify the native Gateway patch against the audited reauth-idle release."""
    from scripts.scoped_consent_query_release import NATIVE_GATEWAY_CHANGED, package_changes

    receipt = json.loads(receipt_path.read_text())
    if (receipt.get("target") != state["target"]
            or receipt.get("stack_id") != state["app"]["stackId"]
            or set(receipt.get("functions", {})) != {"Business", "Worker"}
            or set(receipt.get("base_paths", {})) != {"Business", "Worker"}
            or logical not in ("Business", "Worker")):
        raise ValueError("Scoped native-gateway release is outside the bound target")
    item = receipt["functions"][logical]
    upload = receipt.get("upload", {})
    if (item.get("function") != function_name
            or item.get("changed_zip_members") != NATIVE_GATEWAY_CHANGED
            or receipt.get("operations", {}).get(logical, {}).get("phase") != "VERIFIED"
            or upload.get("digest") != item.get("patch_sha256")
            or upload.get("key") != "journey/native-gateway/" + item.get("patch_sha256", "") + ".zip"):
        raise ValueError("Scoped native-gateway release lacks function or completion proof")
    previous_sha = verified_scoped_reauth_idle_release(
        Path(receipt["previous_receipt"]), state, logical, function_name)
    if item.get("base_sha256") != previous_sha:
        raise ValueError("Scoped native-gateway release predecessor differs")
    if package_changes(Path(receipt["base_paths"][logical]), Path(receipt["package_path"]),
                       previous_sha, item["patch_sha256"], NATIVE_GATEWAY_CHANGED) != NATIVE_GATEWAY_CHANGED:
        raise ValueError("Scoped native-gateway release has unexpected ZIP changes")
    return item["patch_sha256"]


def verified_scoped_callback_resume_release(receipt_path, state, logical, function_name):
    """Verify the consent continuation patch against the native Gateway release."""
    from scripts.scoped_consent_query_release import CALLBACK_RESUME_CHANGED, package_changes

    receipt = json.loads(receipt_path.read_text())
    if (receipt.get("target") != state["target"]
            or receipt.get("stack_id") != state["app"]["stackId"]
            or set(receipt.get("functions", {})) != {"Business", "Worker"}
            or set(receipt.get("base_paths", {})) != {"Business", "Worker"}
            or logical not in ("Business", "Worker")):
        raise ValueError("Scoped callback-resume release is outside the bound target")
    item = receipt["functions"][logical]
    upload = receipt.get("upload", {})
    if (item.get("function") != function_name
            or item.get("changed_zip_members") != CALLBACK_RESUME_CHANGED
            or receipt.get("operations", {}).get(logical, {}).get("phase") != "VERIFIED"
            or upload.get("digest") != item.get("patch_sha256")
            or upload.get("key") != "journey/callback-resume/" + item.get("patch_sha256", "") + ".zip"):
        raise ValueError("Scoped callback-resume release lacks function or completion proof")
    previous_sha = verified_scoped_native_gateway_release(
        Path(receipt["previous_receipt"]), state, logical, function_name)
    if item.get("base_sha256") != previous_sha:
        raise ValueError("Scoped callback-resume release predecessor differs")
    if package_changes(Path(receipt["base_paths"][logical]), Path(receipt["package_path"]),
                       previous_sha, item["patch_sha256"], CALLBACK_RESUME_CHANGED) != CALLBACK_RESUME_CHANGED:
        raise ValueError("Scoped callback-resume release has unexpected ZIP changes")
    return item["patch_sha256"]


def verified_scoped_session_auth_release(receipt_path, state, logical, function_name):
    """Verify session authorization against the deployed consent continuation release."""
    from scripts.scoped_consent_query_release import SESSION_AUTH_CHANGED, package_changes

    receipt = json.loads(receipt_path.read_text())
    if (receipt.get("target") != state["target"]
            or receipt.get("stack_id") != state["app"]["stackId"]
            or set(receipt.get("functions", {})) != {"Business", "Worker"}
            or set(receipt.get("base_paths", {})) != {"Business", "Worker"}
            or logical not in ("Business", "Worker")):
        raise ValueError("Scoped session-auth release is outside the bound target")
    item = receipt["functions"][logical]
    upload = receipt.get("upload", {})
    if (item.get("function") != function_name
            or item.get("changed_zip_members") != SESSION_AUTH_CHANGED
            or receipt.get("operations", {}).get(logical, {}).get("phase") != "VERIFIED"
            or upload.get("digest") != item.get("patch_sha256")
            or upload.get("key") != "journey/session-auth/" + item.get("patch_sha256", "") + ".zip"):
        raise ValueError("Scoped session-auth release lacks function or completion proof")
    previous_sha = verified_scoped_callback_resume_release(
        Path(receipt["previous_receipt"]), state, logical, function_name)
    if item.get("base_sha256") != previous_sha:
        raise ValueError("Scoped session-auth release predecessor differs")
    if package_changes(Path(receipt["base_paths"][logical]), Path(receipt["package_path"]),
                       previous_sha, item["patch_sha256"], SESSION_AUTH_CHANGED) != SESSION_AUTH_CHANGED:
        raise ValueError("Scoped session-auth release has unexpected ZIP changes")
    return item["patch_sha256"]


def audit(state_path, runtime_id=None, existing=False, output=None, scoped_worker_receipt=None,
          scoped_deploy_only_receipt=None, scoped_consent_query_receipt=None,
          scoped_connected_popup_receipt=None, scoped_reauth_receipt=None,
          scoped_reauth_idle_receipt=None, scoped_native_gateway_receipt=None,
          scoped_callback_resume_receipt=None, scoped_session_auth_receipt=None,
          scoped_session_renewal_receipt=None):
    state = deployment_state(json.loads(state_path.read_text()), existing)
    if (scoped_worker_receipt or scoped_deploy_only_receipt or scoped_consent_query_receipt
            or scoped_connected_popup_receipt or scoped_reauth_receipt or scoped_reauth_idle_receipt
            or scoped_native_gateway_receipt or scoped_callback_resume_receipt
            or scoped_session_auth_receipt or scoped_session_renewal_receipt) and not existing:
        raise ValueError("Scoped Lambda release is only valid for the bound existing deployment")
    target = state["target"]
    session = boto3.Session(profile_name=target["profile"], region_name=target["region"])
    if session.client("sts").get_caller_identity()["Account"] != target["account"]:
        raise ValueError("Audit account mismatch")
    clients, results, python_application_status = {}, [], []
    scoped_worker_sha = None
    scoped_deploy_only_sha = {}
    scoped_consent_query_sha = {}
    scoped_connected_popup_sha = {}
    scoped_reauth_sha = {}
    scoped_reauth_idle_sha = {}
    scoped_native_gateway_sha = {}
    scoped_callback_resume_sha = {}
    scoped_session_auth_sha = {}
    scoped_session_renewal_sha = {}
    def client(name):
        if name not in clients:
            clients[name] = session.client(name)
        return clients[name]
    def tags(value):
        return value if isinstance(value, dict) else {t["Key"]: t["Value"] for t in value}
    def record(name, checks):
        results.append({"resource": name, "checks": checks, "pass": all(checks.values())})
    for section in ("artifacts", "journey", "app"):
        stack_id = state[section]["stackId"]
        expected_name = "governed-agent-builder-journey" if existing and section == "journey" else state["prefix"] + "-" + section
        if stack_id.split("/")[1] != expected_name:
            raise ValueError("Audit stack outside the bound deployment")
        stack = client("cloudformation").describe_stacks(StackName=stack_id)["Stacks"][0]
        record(section, {"terminal_success": stack["StackStatus"] in ("CREATE_COMPLETE", "UPDATE_COMPLETE"),
                         "retention_tag": tags(stack["Tags"]).get("auto-delete") == "no"})
        resources = client("cloudformation").list_stack_resources(StackName=stack_id)["StackResourceSummaries"]
        for resource in resources:
            kind, physical = resource["ResourceType"], resource["PhysicalResourceId"]
            label = section + "/" + resource["LogicalResourceId"]
            checks, actual_tags = {}, None
            if kind == "AWS::S3::Bucket":
                c = client("s3")
                actual_tags = c.get_bucket_tagging(Bucket=physical)["TagSet"]
                checks.update(public_access_blocked=all(c.get_public_access_block(Bucket=physical)["PublicAccessBlockConfiguration"].values()),
                              encrypted=bool(c.get_bucket_encryption(Bucket=physical)["ServerSideEncryptionConfiguration"]["Rules"]),
                              private_policy=not c.get_bucket_policy_status(Bucket=physical)["PolicyStatus"]["IsPublic"])
            elif kind == "AWS::DynamoDB::Table":
                c = client("dynamodb"); value = c.describe_table(TableName=physical)["Table"]
                actual_tags = c.list_tags_of_resource(ResourceArn=value["TableArn"])["Tags"]
                checks.update(active=value["TableStatus"] == "ACTIVE", encrypted=value.get("SSEDescription", {}).get("Status", "ENABLED") == "ENABLED")
            elif kind == "AWS::IAM::Role":
                c = client("iam"); value = c.get_role(RoleName=physical)["Role"]
                actual_tags = value.get("Tags", [])
                policies = [c.get_role_policy(RoleName=physical, PolicyName=p)["PolicyDocument"]
                            for p in c.list_role_policies(RoleName=physical)["PolicyNames"]]
                checks["no_administrator_wildcard"] = not any(
                    s["Effect"] == "Allow" and s["Action"] in ("*", ["*"]) for p in policies for s in p["Statement"])
                checks["inline_policy_limit"] = sum(len(json.dumps(p, separators=(",", ":"))) for p in policies) < 10240
            elif kind == "AWS::IAM::ManagedPolicy":
                c = client("iam")
                value = c.get_policy(PolicyArn=physical)["Policy"]
                actual_tags = c.list_policy_tags(PolicyArn=physical)["Tags"]
                policy = c.get_policy_version(PolicyArn=physical, VersionId=value["DefaultVersionId"])["PolicyVersion"]["Document"]
                checks["managed_policy_limit"] = len(json.dumps(policy, separators=(",", ":"))) < 6144
                checks["retention_tag"] = tags(actual_tags).get("auto-delete") == "no"
                checks["no_administrator_wildcard"] = not any(
                    s["Effect"] == "Allow" and s["Action"] in ("*", ["*"]) for s in policy["Statement"])
            elif kind == "AWS::Lambda::Function":
                c = client("lambda"); value = c.get_function_configuration(FunctionName=physical)
                actual_tags = c.list_tags(Resource=value["FunctionArn"])["Tags"]
                checks.update(active=value["State"] == "Active" and value.get("LastUpdateStatus") == "Successful",
                              no_demo_mode=value.get("Environment", {}).get("Variables", {}).get("DEMO_MODE") != "1")
                if section == "app":
                    expected_sha = state["release"]["sha256"]
                    logical = resource["LogicalResourceId"]
                    if logical in ("Auth", "Authorizer", "Business", "Worker") and scoped_session_renewal_receipt:
                        from scripts.scoped_session_release import verify_release
                        expected_sha = verify_release(scoped_session_renewal_receipt, state, logical, physical)
                        scoped_session_renewal_sha[logical] = expected_sha
                        checks["scoped_session_renewal_patch_verified"] = True
                    elif logical in ("Business", "Worker") and scoped_session_auth_receipt:
                        expected_sha = verified_scoped_session_auth_release(
                            scoped_session_auth_receipt, state, logical, physical)
                        scoped_session_auth_sha[logical] = expected_sha
                        checks["scoped_session_auth_patch_verified"] = True
                    elif logical in ("Business", "Worker") and scoped_callback_resume_receipt:
                        expected_sha = verified_scoped_callback_resume_release(
                            scoped_callback_resume_receipt, state, logical, physical)
                        scoped_callback_resume_sha[logical] = expected_sha
                        checks["scoped_callback_resume_patch_verified"] = True
                    elif logical in ("Business", "Worker") and scoped_native_gateway_receipt:
                        expected_sha = verified_scoped_native_gateway_release(
                            scoped_native_gateway_receipt, state, logical, physical)
                        scoped_native_gateway_sha[logical] = expected_sha
                        checks["scoped_native_gateway_patch_verified"] = True
                    elif logical in ("Business", "Worker") and scoped_reauth_idle_receipt:
                        expected_sha = verified_scoped_reauth_idle_release(
                            scoped_reauth_idle_receipt, state, logical, physical)
                        scoped_reauth_idle_sha[logical] = expected_sha
                        checks["scoped_reauth_idle_patch_verified"] = True
                    elif logical in ("Business", "Worker") and scoped_reauth_receipt:
                        expected_sha = verified_scoped_reauth_release(
                            scoped_reauth_receipt, state, logical, physical)
                        scoped_reauth_sha[logical] = expected_sha
                        checks["scoped_reauth_patch_verified"] = True
                    elif logical in ("Business", "Worker") and scoped_connected_popup_receipt:
                        expected_sha = verified_scoped_connected_popup_release(
                            scoped_connected_popup_receipt, state, logical, physical)
                        scoped_connected_popup_sha[logical] = expected_sha
                        checks["scoped_connected_popup_patch_verified"] = True
                    elif logical in ("Business", "Worker") and scoped_consent_query_receipt:
                        expected_sha = verified_scoped_consent_query_release(
                            scoped_consent_query_receipt, state, logical, physical)
                        scoped_consent_query_sha[logical] = expected_sha
                        checks["scoped_consent_query_patch_verified"] = True
                    elif logical in ("Business", "Worker") and scoped_deploy_only_receipt:
                        expected_sha = verified_scoped_deploy_only_release(
                            scoped_deploy_only_receipt, state, logical, physical)
                        scoped_deploy_only_sha[logical] = expected_sha
                        checks["scoped_deploy_only_patch_verified"] = True
                    elif logical == "Worker" and scoped_worker_receipt:
                        scoped_worker_sha = verified_scoped_worker_release(scoped_worker_receipt, state, physical)
                        expected_sha = scoped_worker_sha
                        checks["scoped_worker_patch_verified"] = True
                    checks["release_digest"] = value["CodeSha256"] == base64.b64encode(bytes.fromhex(expected_sha)).decode()
                try:
                    c.get_function_url_config(FunctionName=physical); checks["no_function_url"] = False
                except ClientError as exc:
                    if exc.response["Error"]["Code"] != "ResourceNotFoundException":
                        raise
                    checks["no_function_url"] = True
            elif kind == "AWS::Lambda::EventSourceMapping":
                c = client("lambda"); value = c.get_event_source_mapping(UUID=physical)
                actual_tags = c.list_tags(Resource=value["EventSourceMappingArn"])["Tags"]
            elif kind == "AWS::Logs::LogGroup":
                arn = f"arn:aws:logs:{target['region']}:{target['account']}:log-group:{physical}"
                actual_tags = client("logs").list_tags_for_resource(resourceArn=arn)["tags"]
            elif kind == "AWS::SQS::Queue":
                c = client("sqs")
                actual_tags = c.list_queue_tags(QueueUrl=physical)["Tags"]
                checks["encrypted"] = c.get_queue_attributes(QueueUrl=physical, AttributeNames=["SqsManagedSseEnabled"])["Attributes"]["SqsManagedSseEnabled"] == "true"
            elif kind == "AWS::CloudFront::Distribution":
                c = client("cloudfront"); value = c.get_distribution(Id=physical)["Distribution"]
                actual_tags = c.list_tags_for_resource(Resource=value["ARN"])["Tags"]["Items"]
                checks.update(deployed=value["Status"] == "Deployed",
                              private_s3_origin=all(o.get("OriginAccessControlId") for o in value["DistributionConfig"]["Origins"]["Items"] if "S3OriginConfig" in o))
            elif kind == "AWS::CloudFront::Function":
                c = client("cloudfront"); value = c.describe_function(Name=physical.rsplit("/", 1)[-1], Stage="LIVE")
                actual_tags = c.list_tags_for_resource(Resource=value["FunctionSummary"]["FunctionMetadata"]["FunctionARN"])["Tags"]["Items"]
            elif kind == "AWS::ApiGatewayV2::Api":
                c = client("apigatewayv2")
                actual_tags = c.get_api(ApiId=physical).get("Tags", {})
                routes = c.get_routes(ApiId=physical)["Items"]
                checks["business_routes_authorized"] = all(r.get("AuthorizationType") != "NONE" for r in routes
                    if r["RouteKey"].startswith(("ANY /api", "GET /api", "POST /api")) and r["RouteKey"] != "POST /api/auth/logout")
            elif kind == "AWS::ApiGatewayV2::Stage":
                api_id, stage_name = physical.split("|") if "|" in physical else (state["app"]["outputs"]["ApiEndpoint"].split("//")[1].split(".")[0], "$default")
                actual_tags = client("apigatewayv2").get_stage(ApiId=api_id, StageName=stage_name).get("Tags", {})
            elif kind == "AWS::CloudWatch::Alarm":
                c = client("cloudwatch")
                value = c.describe_alarms(AlarmNames=[physical])["MetricAlarms"][0]
                actual_tags = c.list_tags_for_resource(ResourceARN=value["AlarmArn"])["Tags"]
            elif kind == "AWS::Cognito::UserPoolClient":
                outputs = state["app"]["outputs"]
                value = client("cognito-idp").describe_user_pool_client(UserPoolId=outputs["UserPoolId"], ClientId=physical)["UserPoolClient"]
                checks.update(code_flow_only=value["AllowedOAuthFlows"] == ["code"], no_client_secret="ClientSecret" not in value,
                              bound_callback_only=value["CallbackURLs"] == [outputs["ApplicationOrigin"] + "/auth/callback"])
            elif kind == "AWS::Cognito::UserPool":
                actual_tags = client("cognito-idp").describe_user_pool(UserPoolId=physical)["UserPool"].get("UserPoolTags", {})
            if kind in TAG_PROPERTIES:
                checks["retention_tag"] = actual_tags is not None and tags(actual_tags).get("auto-delete") == "no"
            if checks:
                record(label, checks)
    c = client("bedrock-agentcore-control")
    gateway = c.get_gateway(gatewayIdentifier=state["gateway"]["gatewayId"])
    record("Gateway", {"ready": gateway["status"] == "READY", "iam_inbound": gateway["authorizerType"] == "AWS_IAM",
                       "retention_tag": c.list_tags_for_resource(resourceArn=gateway["gatewayArn"])["tags"].get("auto-delete") == "no"})
    record("GatewayIdentity", {"retention_tag": c.list_tags_for_resource(resourceArn=state["gateway_identity_tagged"])["tags"].get("auto-delete") == "no"})
    from scripts.agent_network_audit import approved_network, business_agent_checks, runtime_network_checks
    record("BusinessAgentNetwork", business_agent_checks(state))
    registry = client("agent-registry-control")
    reg = registry.get_registry(registryId=state["registry"]["registryId"])
    record("Registry", {"ready": reg["status"] == "READY", "manual_approval": not reg.get("approvalConfiguration", {}).get("autoApprovalRules"),
                        "retention_tag": registry.list_tags_for_resource(resourceArn=reg["registryArn"])["tags"].get("auto-delete") == "no"})
    page = registry.list_registry_records(registryId=state["registry"]["registryId"])
    if page.get("nextToken"):
        raise ValueError("Registry audit pagination required")
    for entry in page["registryRecords"]:
        record("RegistryRecord/" + entry["recordId"], {"retention_tag": registry.list_tags_for_resource(resourceArn=entry["recordArn"])["tags"].get("auto-delete") == "no"})
    for connection in state["journeyPlatform"]["mcp_onboarding"]["connections"]:
        provider = connection["configuration"].get("credentialProvider", {})
        for configuration in provider.values():
            if configuration.get("providerArn"):
                record("CredentialConnection/" + connection["id"], {
                    "retention_tag": c.list_tags_for_resource(resourceArn=configuration["providerArn"])["tags"].get("auto-delete") == "no"})
    for secret in state["journeyPlatform"]["mcp_onboarding"].get("secret_arns", []):
        value = client("secretsmanager").describe_secret(SecretId=secret)
        record("CredentialSecret/" + value["Name"], {
            "retention_tag": tags(value.get("Tags", [])).get("auto-delete") == "no"})
    if existing and state["journeyPlatform"]["mcp_onboarding"].get("credential_prefix"):
        from backend.dynamo_store import DynamoStore
        from backend.mcp_credentials import CredentialCloud
        store = DynamoStore(state["app"]["outputs"]["StateTable"], session.resource("dynamodb"))
        with store.tx() as db:
            requests = [json.loads(r["body"]) for r in db.select("settings") if r["key"].startswith("mcp-auth-request:")]
            connections = {r["key"]: json.loads(r["body"]) for r in db.select("settings") if r["key"].startswith("mcp-auth:")}
        credential_cloud = CredentialCloud(state["journeyPlatform"], session=session)
        from backend.mcp_user_oauth import OAuthCloud
        oauth_cloud = OAuthCloud(state["journeyPlatform"], control=client("bedrock-agentcore-control"))
        auth = SimpleNamespace(public_url=state["app"]["outputs"]["ApplicationOrigin"])
        for request in requests:
            try:
                if request.get("auth_type") in ("GATEWAY_IAM_ROLE", "OAUTH") and not request.get("secret_name"):
                    connection = connections.get("mcp-auth:" + request["connection_id"])
                    record("AuthenticationReference/" + request["id"],
                           reference_checks(request, connection, state["journeyPlatform"], auth, oauth_cloud))
                    continue
                if request["phase"] == "DELETED":
                    record("DeletedSelfServiceCredential/" + request["id"], {
                        "provider_absent": credential_cloud.management_read("provider_delete", request, {}),
                        "secret_absent_or_recoverable": credential_cloud.management_read("secret_delete", request, {})})
                    continue
                secret_arn = credential_cloud.read_secret(request)
                provider = credential_cloud.read_provider({**request, "secret_arn": secret_arn}) if secret_arn else None
                record("SelfServiceCredential/" + request["id"], {
                    "deployment_binding": request["deployment_prefix"] == state["journeyPlatform"]["mcp_onboarding"]["credential_prefix"],
                    "secret_identity_version_and_tags": bool(secret_arn),
                    "external_provider_binding_and_tags": bool(provider)})
            except Exception:
                record("SelfServiceCredential/" + request["id"], {"native_binding_verified": False})
        from backend.mcp_python_cloud import PythonCloud, configuration
        from backend.foundation_runs import get
        with store.tx() as db:
            python_config = get(db, "mcp-python-config")
            python_servers = [json.loads(r["body"]) for r in db.select("settings") if r["key"].startswith("mcp-python:")]
        # Package uploads are optional, but a fresh install must never leave their
        # absence silent, and a half-installed state must fail with the exact
        # remaining step (docs/generic-mcp-onboarding.md, "Operators bind package
        # permissions").
        package_binding = state["journeyPlatform"].get("mcp_package_upload")
        if not python_config and not package_binding:
            record("PackageUploadInstallation", {
                "status": "NOT_INSTALLED",
                "action": "Optional feature. To enable Upload MCP package (.zip): install python "
                          "hosting with examples/runtime-snowflake-mcp/facade_deploy.py plan/deploy/"
                          "audit/configure --python-config, then scripts.mcp_onboarding_deploy "
                          "configure_packages, deploy and publish."})
        else:
            from backend.mcp_python_cloud import artifact_bucket
            record("PackageUploadInstallation", {
                "python_config_installed": bool(python_config),
                "package_binding_installed": bool(package_binding),
                "binding_matches_python_config": bool(python_config) and bool(package_binding)
                    and package_binding["bucket"] == artifact_bucket(python_config)
                    and package_binding["runtime_prefix"] == python_config["runtime_prefix"]})
        if python_config:
            configuration(python_config, state["journeyPlatform"])
            python_cloud = PythonCloud(state["journeyPlatform"], session=session)
            for server in python_servers:
                python_application_status.append({k: server.get(k) for k in ("id", "phase", "stage", "failure_code")})
                try:
                    record("PythonMcp/" + server["id"], {**python_resource_checks(python_cloud, server, python_config),
                        **runtime_network_checks(python_cloud.control, server, approved_network(state))})
                except Exception:
                    record("PythonMcp/" + server["id"], {"native_binding_verified": False})
    if runtime_id:
        value = c.get_agent_runtime(agentRuntimeId=runtime_id)
        if value["roleArn"] != state["journeyPlatform"]["runtime_role"]:
            raise ValueError("Runtime does not belong to the bound deployment role")
        endpoint = c.get_agent_runtime_endpoint(agentRuntimeId=runtime_id, endpointName="DEFAULT")
        record("TestAgentRuntime", {
            "ready": value["status"] == "READY",
            "iam_invocation": not value.get("authorizerConfiguration"),
            **runtime_network_checks(c, {"runtime_id": runtime_id, "runtime_version": value["agentRuntimeVersion"]},
                                     approved_network(state)),
            "retention_tag": c.list_tags_for_resource(resourceArn=value["agentRuntimeArn"])["tags"].get("auto-delete") == "no"})
        record("TestAgentEndpoint", {
            "ready": endpoint["status"] == "READY" and endpoint["liveVersion"] == value["agentRuntimeVersion"],
            "retention_tag": c.list_tags_for_resource(resourceArn=endpoint["agentRuntimeEndpointArn"])["tags"].get("auto-delete") == "no"})
        record("TestAgentIdentity", {
            "retention_tag": c.list_tags_for_resource(resourceArn=value["workloadIdentityDetails"]["workloadIdentityArn"])["tags"].get("auto-delete") == "no"})
        log_name = "/aws/bedrock-agentcore/runtimes/" + runtime_id + "-DEFAULT"
        log_arn = f"arn:aws:logs:{target['region']}:{target['account']}:log-group:{log_name}"
        group = next(g for g in client("logs").describe_log_groups(logGroupNamePrefix=log_name)["logGroups"] if g["logGroupName"] == log_name)
        record("TestAgentLogs", {
            "retention_14_days": group.get("retentionInDays") == 14,
            "retention_tag": client("logs").list_tags_for_resource(resourceArn=log_arn)["tags"].get("auto-delete") == "no"})
    for role in ("admin", "business"):
        if not state.get("qa-" + role):
            continue
        for part in ("username", "password"):
            name = state["qa-" + role]["parameterPrefix"] + "/" + part
            record("QAParameter/" + role + "/" + part, {"retention_tag": tags(client("ssm").list_tags_for_resource(ResourceType="Parameter", ResourceId=name)["TagList"]).get("auto-delete") == "no"})
    if scoped_worker_receipt and not scoped_worker_sha:
        raise ValueError("Scoped Worker release did not identify the bound Worker Lambda")
    if scoped_deploy_only_receipt and set(scoped_deploy_only_sha) != {"Business", "Worker"}:
        raise ValueError("Scoped deploy-only release did not identify both bound Lambda functions")
    if scoped_consent_query_receipt and set(scoped_consent_query_sha) != {"Business", "Worker"}:
        raise ValueError("Scoped consent-query release did not identify both bound Lambda functions")
    if scoped_connected_popup_receipt and set(scoped_connected_popup_sha) != {"Business", "Worker"}:
        raise ValueError("Scoped connected-popup release did not identify both bound Lambda functions")
    if scoped_reauth_receipt and set(scoped_reauth_sha) != {"Business", "Worker"}:
        raise ValueError("Scoped reauth release did not identify both bound Lambda functions")
    if scoped_reauth_idle_receipt and set(scoped_reauth_idle_sha) != {"Business", "Worker"}:
        raise ValueError("Scoped reauth-idle release did not identify both bound Lambda functions")
    if scoped_native_gateway_receipt and set(scoped_native_gateway_sha) != {"Business", "Worker"}:
        raise ValueError("Scoped native-gateway release did not identify both bound Lambda functions")
    if scoped_callback_resume_receipt and set(scoped_callback_resume_sha) != {"Business", "Worker"}:
        raise ValueError("Scoped callback-resume release did not identify both bound Lambda functions")
    if scoped_session_auth_receipt and set(scoped_session_auth_sha) != {"Business", "Worker"}:
        raise ValueError("Scoped session-auth release did not identify both bound Lambda functions")
    if scoped_session_renewal_receipt and set(scoped_session_renewal_sha) != {"Auth", "Authorizer", "Business", "Worker"}:
        raise ValueError("Scoped session renewal did not identify all four bound Lambda functions")
    report = {"pass": all(r["pass"] for r in results), "resource_checks": results,
              "python_application_status": python_application_status,
              **({"scoped_worker_sha256": scoped_worker_sha} if scoped_worker_sha else {}),
              **({"scoped_deploy_only_sha256": scoped_deploy_only_sha} if scoped_deploy_only_sha else {}),
              **({"scoped_consent_query_sha256": scoped_consent_query_sha} if scoped_consent_query_sha else {}),
              **({"scoped_connected_popup_sha256": scoped_connected_popup_sha} if scoped_connected_popup_sha else {}),
              **({"scoped_reauth_sha256": scoped_reauth_sha} if scoped_reauth_sha else {}),
              **({"scoped_reauth_idle_sha256": scoped_reauth_idle_sha} if scoped_reauth_idle_sha else {}),
              **({"scoped_native_gateway_sha256": scoped_native_gateway_sha} if scoped_native_gateway_sha else {}),
              **({"scoped_callback_resume_sha256": scoped_callback_resume_sha} if scoped_callback_resume_sha else {}),
              **({"scoped_session_auth_sha256": scoped_session_auth_sha} if scoped_session_auth_sha else {}),
              **({"scoped_session_renewal_sha256": scoped_session_renewal_sha} if scoped_session_renewal_sha else {})}
    (output or state_path.parent / "security-after.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"pass": report["pass"], "resources_checked": len(results),
                      "incomplete_python_deployments": [s for s in python_application_status if s["phase"] != "READY"],
                      "failures": [r for r in results if not r["pass"]]}))
    if not report["pass"]:
        raise RuntimeError("Deployment security/tag validation failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--runtime-id")
    parser.add_argument("--existing", action="store_true", help="Audit the existing canonical Studio deployment")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--scoped-worker-receipt", type=Path)
    parser.add_argument("--scoped-deploy-only-receipt", type=Path)
    parser.add_argument("--scoped-consent-query-receipt", type=Path)
    parser.add_argument("--scoped-connected-popup-receipt", type=Path)
    parser.add_argument("--scoped-reauth-receipt", type=Path)
    parser.add_argument("--scoped-reauth-idle-receipt", type=Path)
    parser.add_argument("--scoped-native-gateway-receipt", type=Path)
    parser.add_argument("--scoped-callback-resume-receipt", type=Path)
    parser.add_argument("--scoped-session-auth-receipt", type=Path)
    parser.add_argument("--scoped-session-renewal-receipt", type=Path)
    args = parser.parse_args()
    audit(args.state.resolve(), args.runtime_id, args.existing, args.output, args.scoped_worker_receipt,
          args.scoped_deploy_only_receipt, args.scoped_consent_query_receipt,
          args.scoped_connected_popup_receipt, args.scoped_reauth_receipt,
          args.scoped_reauth_idle_receipt, args.scoped_native_gateway_receipt,
          args.scoped_callback_resume_receipt, args.scoped_session_auth_receipt,
          args.scoped_session_renewal_receipt)
