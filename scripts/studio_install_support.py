"""Receipts and native boundaries used only by the fresh-account installer."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import quote

from foundation_harness.config import digest
from scripts.bootstrap_support import Journal, NoRetrySession, PendingOperation, PlatformTarget, json_file, missing
from scripts.deployment_target import PREFIX
from scripts.journey_platform import wait
from scripts.serverless_deploy import safety

STACK_TAGS = {"project": "governed-agent-builder", "architecture": "managed-serverless", "auto-delete": "no"}


def source_manifest(root):
    """Pin source files, excluding workstation files and generated output."""
    directories = (
        "backend", "foundation_harness", "foundations", "frontend", "infra",
        "runtime", "scripts", "tools", "examples/journey", "examples/runtime-snowflake-mcp",
    )
    ignored = {"node_modules", ".venv", "dist", "__pycache__", "artifacts", ".git", ".pytest_cache",
               "work", "test-results", "playwright-report"}
    suffixes = {".py", ".json", ".ts", ".tsx", ".css", ".html", ".toml", ".lock"}
    files = [root / name for name in ("pyproject.toml", "uv.lock") if (root / name).is_file()]
    for directory in directories:
        for current, children, names in os.walk(root / directory):
            children[:] = [name for name in children if name not in ignored and not name.startswith(".")]
            files.extend(Path(current) / name for name in names if Path(name).suffix in suffixes)
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(files)}


def source_digest(root):
    return digest(source_manifest(root))


class InstallTarget(PlatformTarget):
    """Reconcile only this installer's recorded base-stack submissions."""
    def __init__(self, config, source_sha, session_factory=None):
        if config.state.exists():
            saved = json_file(config.state)
            prior = saved.get("installation")
            if prior is None and set(saved) - {"target"}:
                raise RuntimeError("Existing deployment has no installation receipt; refusing adoption")
            if prior and (prior["configuration"] != config.intent() or prior["source_sha256"] != source_sha):
                raise RuntimeError("Installation configuration or source changed; restore the pinned inputs")
        super().__init__(config.account, config.profile, config.region, config.state,
                         session_factory=session_factory)
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as output:
                json.dump(self.state, output)
                output.flush()
                os.fsync(output.fileno())
        if "installation" not in self.state:
            self.save("installation", {"configuration": config.intent(), "source_sha256": source_sha,
                                      "steps": {}, "user_acceptance": "NOT_RUN"})
        self.session = NoRetrySession(self.session, self)
        self.cf = self.session.client("cloudformation")

    def check_stacks(self):
        self.reconciled_rollback_stack_id = None
        recover_stacks(self)
        super().check_stacks()


def stack_intent(section, body, parameters, *, prior_stack=None):
    return {"kind": "studio-install-stack", "section": section, "stack_name": PREFIX + "-" + section,
            "template": body, "parameters": parameters, "prior_stack_id": prior_stack}


def _accepted_stack(target, record):
    """A lost acknowledgment needs AWS's request token, not a matching name."""
    request, cf = record["request"], target.cf
    current = missing(lambda: cf.describe_stacks(StackName=request["stack_name"])["Stacks"][0])
    if current is None:
        return None
    target.check_stack(current)
    if request["prior_stack_id"] and current["StackId"] != request["prior_stack_id"]:
        raise RuntimeError("Recorded application identity changed")
    parameters = {"StackName": current["StackId"]}
    for _ in range(100):
        page = cf.describe_stack_events(**parameters)
        if any(event.get("ResourceType") == "AWS::CloudFormation::Stack"
               and event.get("ClientRequestToken") == record["request_token"]
               for event in page["StackEvents"]):
            return {"stack_id": current["StackId"]}
        if not page.get("NextToken"):
            return None
        parameters["NextToken"] = page["NextToken"]
    raise RuntimeError("Incomplete CloudFormation request-token inventory")


class ActivationRolledBack(RuntimeError):
    """A failed activation restored the exact previously installed application."""


def _record_activation_rollback(target, phase, request, receipt, current):
    base = target.state.get("platformOperations", {}).get("install-stack-app", {}).get("request")
    recorded = target.state.get("installationStacks", {}).get("app", {})
    previous = target.state.get("app", {})
    if (not phase.startswith("activate") or request["section"] != "app"
            or request.get("prior_stack_id") != receipt["stack_id"]
            or current["StackId"] != receipt["stack_id"]
            or previous.get("stackId") != receipt["stack_id"] or not base
            or not recorded.get("verified") or recorded.get("stack_id") != receipt["stack_id"]):
        raise RuntimeError("No owned successful application baseline for this rollback")
    target.check_stack(current)
    actual = target.cf.get_template(StackName=current["StackId"])["TemplateBody"]
    actual = json.loads(actual) if isinstance(actual, str) else actual
    if (actual != base["template"] or digest(actual) != recorded["template_digest"]
            or {p["ParameterKey"]: p.get("ParameterValue") for p in current.get("Parameters", [])}
            != base["parameters"]
            or {o["OutputKey"]: o["OutputValue"] for o in current.get("Outputs", [])} != previous["outputs"]):
        raise RuntimeError("Rollback did not restore the recorded application")
    operation = target.state["platformOperations"]["install-stack-" + phase]
    events, parameters = [], {"StackName": current["StackId"]}
    for _ in range(100):
        page = target.cf.describe_stack_events(**parameters)
        events.extend(page["StackEvents"])
        if not page.get("NextToken"):
            break
        parameters["NextToken"] = page["NextToken"]
    else:
        raise RuntimeError("Incomplete rollback event inventory")
    roots = [event for event in events if event.get("ResourceType") == "AWS::CloudFormation::Stack"
             and event.get("PhysicalResourceId") == current["StackId"]]
    if (not roots or roots[0].get("ResourceStatus") != "UPDATE_ROLLBACK_COMPLETE"
            or roots[0].get("ClientRequestToken") != operation["request_token"]):
        raise RuntimeError("Rollback belongs to a different native update")
    limit = None
    for event in events:
        if (event.get("LogicalResourceId") == "Worker" and event.get("ResourceStatus") == "UPDATE_FAILED"
                and event.get("ClientRequestToken") == operation["request_token"]):
            match = re.search(r"MemorySize.*?less than or equal to (\d+)", event.get("ResourceStatusReason", ""))
            if match:
                limit = int(match.group(1))
                break
    resolution = {
        "stack_id": current["StackId"], "status": "UPDATE_ROLLBACK_COMPLETE",
        "request_token": operation["request_token"], "event_id": roots[0]["EventId"],
        "restored_template_digest": digest(actual), "worker_memory_limit": limit,
    }
    rollbacks = copy.deepcopy(target.state.get("installationRollbacks", {}))
    rollbacks[phase] = resolution
    target.save("installationRollbacks", rollbacks)
    target.reconciled_rollback_stack_id = current["StackId"]


def _finish_stack(target, phase, request, receipt):
    def read():
        current = target.cf.describe_stacks(StackName=receipt["stack_id"])["Stacks"][0]
        if current["StackStatus"] == "UPDATE_ROLLBACK_COMPLETE":
            _record_activation_rollback(target, phase, request, receipt, current)
            raise ActivationRolledBack("Activation rolled back; inspect its retained failure before recovery")
        return current

    current = wait(read)
    target.check_stack(current)
    actual = target.cf.get_template(StackName=current["StackId"])["TemplateBody"]
    actual = json.loads(actual) if isinstance(actual, str) else actual
    if (actual != request["template"] or current["StackId"] != receipt["stack_id"]
            or {p["ParameterKey"]: p.get("ParameterValue") for p in current.get("Parameters", [])}
            != request["parameters"]):
        raise RuntimeError("Installed stack differs from its recorded template or parameters")
    outputs = {item["OutputKey"]: item["OutputValue"] for item in current.get("Outputs", [])}
    previous = target.state.get(request["section"])
    if previous and (previous["stackId"] != current["StackId"] or previous["outputs"] != outputs):
        raise RuntimeError("Application identity changed during installation")
    target.save(request["section"], {"stackId": current["StackId"], "status": current["StackStatus"],
                                     "outputs": outputs})
    completed = copy.deepcopy(target.state.get("installationStacks", {}))
    completed[phase] = {"verified": True, "stack_id": current["StackId"], "template_digest": digest(actual)}
    target.save("installationStacks", completed)
    return outputs


def recover_stacks(target):
    for name, record in list(target.state.get("platformOperations", {}).items()):
        if not name.startswith("install-stack-"):
            continue
        phase = name.removeprefix("install-stack-")
        if target.state.get("installationStacks", {}).get(phase, {}).get("verified"):
            continue
        request = record["request"]
        if request.get("kind") != "studio-install-stack" or request["section"] not in ("artifacts", "app"):
            raise RuntimeError("Invalid retained installation stack intent")
        rollback = target.state.get("installationRollbacks", {}).get(phase)
        if rollback:
            if (rollback["request_token"] != record["request_token"]
                    or rollback["stack_id"] != record["result"]["stack_id"]):
                raise RuntimeError("Retained rollback identity changed")
            # A new, separately journaled update owns current readiness. Keep
            # the failed update's receipt without trying to finish it again.
            later = [value for key, value in target.state.get("platformOperations", {}).items()
                     if key.startswith("install-stack-activate") and key != name
                     and value["request"].get("replaces_phase") == phase]
            if later:
                continue

        def no_replay(token):
            raise AssertionError("Recovery must never resubmit a stack")

        receipt = Journal(target).run(name, request, no_replay, lambda saved: _accepted_stack(target, saved))
        try:
            _finish_stack(target, phase, request, receipt)
        except ActivationRolledBack:
            # This only succeeds after exact native rollback verification.
            # The normal deployment path still refuses the failed intent.
            continue


def upload_artifact(target, label, key, content, *, bucket=None, content_type=None, cache_control=None):
    """Immutable, versioned upload; reconcile uncertainty without another PUT."""
    bucket = bucket or target.state["artifacts"]["outputs"]["Bucket"]
    if bucket not in (target.state["artifacts"]["outputs"]["Bucket"],
                       target.state.get("app", {}).get("outputs", {}).get("FrontendBucket")):
        raise RuntimeError("Upload bucket is not owned by this installation")
    sha = hashlib.sha256(content).hexdigest()
    checksum = base64.b64encode(bytes.fromhex(sha)).decode()
    request = {"bucket": bucket, "key": key, "sha256": sha, "size": len(content)}
    extra = {}
    if content_type:
        extra["ContentType"] = content_type
    if cache_control:
        extra["CacheControl"] = cache_control
    if extra:
        request["properties"] = extra
    name, s3 = "install-upload-" + label, target.session.client("s3")

    def read(record):
        head = missing(lambda: s3.head_object(Bucket=bucket, Key=key, ChecksumMode="ENABLED"))
        if head is None:
            return None
        if (head.get("Metadata") != {"sha256": sha, "request-token": record["request_token"]}
                or head.get("ChecksumSHA256") != checksum or head.get("ContentLength") != len(content)
                or head.get("ServerSideEncryption") != "AES256" or head.get("VersionId") in (None, "null")
                or any(head.get(field) != value for field, value in extra.items())):
            raise RuntimeError("Immutable upload differs from its retained intent")
        return {"bucket": bucket, "key": key, "version": head["VersionId"], "digest": sha}

    if name not in target.state.get("platformOperations", {}):
        existing = missing(lambda: s3.head_object(Bucket=bucket, Key=key, ChecksumMode="ENABLED"))
        if existing is not None:
            raise RuntimeError("Refusing to adopt an unrecorded upload")

    def write(token):
        s3.put_object(Bucket=bucket, Key=key, Body=content, IfNoneMatch="*", ServerSideEncryption="AES256",
                      ChecksumSHA256=checksum, Metadata={"sha256": sha, "request-token": token},
                      Tagging="auto-delete=no", **extra)
        result = read({"request_token": token})
        if result is None:
            raise PendingOperation("Uploaded version is not yet readable")
        return result

    receipt = Journal(target).run(name, request, write, read)
    if read(target.state["platformOperations"][name]) != receipt:
        raise RuntimeError("Pinned upload version changed")
    return receipt


def template_input(target, phase, body):
    content = json.dumps(body, separators=(",", ":")).encode()
    if len(content) <= 51200:
        return {"TemplateBody": content.decode()}
    key = f"templates/install/{phase}/{hashlib.sha256(content).hexdigest()}.json"
    artifact = upload_artifact(target, "template-" + phase, key, content)
    return {"TemplateURL": f"https://{artifact['bucket']}.s3.{target.binding['region']}.amazonaws.com/"
                           f"{key}?versionId={quote(artifact['version'], safe='')}"}


def install_stack(target, section, body, parameters=None, *, phase=None):
    phase, parameters = phase or section, parameters or {}
    target.check_stacks()
    safety(body)
    previous = target.state.get(section)
    operation = "install-stack-" + phase
    recorded = target.state.get("platformOperations", {}).get(operation)
    prior = previous["stackId"] if previous and not recorded else None
    if recorded:
        prior = recorded["request"]["prior_stack_id"]
    request = stack_intent(section, body, parameters, prior_stack=prior)
    recovery = target.state.get("installationMemoryRecovery", {}).get("request", {})
    if phase == recovery.get("activation_phase"):
        if digest(body) != recovery["template_digest"] or parameters != recovery["parameters"]:
            raise RuntimeError("Recovery activation differs from the approved worker-memory change")
        request["replaces_phase"] = recovery["replaces_phase"]
    if phase in target.state.get("installationRollbacks", {}):
        raise ActivationRolledBack("Activation rolled back; inspect its retained failure before recovery")
    if not recorded:
        current = missing(lambda: target.cf.describe_stacks(StackName=request["stack_name"])["Stacks"][0])
        if current and (not previous or current["StackId"] != previous["stackId"]):
            raise RuntimeError("Refusing to adopt an unrecorded stack")
        if previous and not current:
            raise RuntimeError("Recorded stack is missing")
    native_template = template_input(target, phase, body)

    def write(token):
        target.cf.validate_template(**native_template)
        native = {"StackName": prior or request["stack_name"], **native_template,
                  "Parameters": [{"ParameterKey": key, "ParameterValue": value} for key, value in parameters.items()],
                  "Capabilities": ["CAPABILITY_IAM"], "ClientRequestToken": token,
                  "Tags": [{"Key": key, "Value": value} for key, value in STACK_TAGS.items()]}
        result = target.cf.update_stack(**native) if prior else target.cf.create_stack(**native, DisableRollback=True)
        return {"stack_id": result["StackId"]}

    receipt = Journal(target).run(operation, request, write, lambda saved: _accepted_stack(target, saved))
    return _finish_stack(target, phase, request, receipt)
