"""Durable, target-bound infrastructure for initial platform installation.

No provider is selected here. Installation creates a Gateway without targets or
credentials; existing installations retain their protected deployment state.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

from botocore.config import Config
from botocore.exceptions import ClientError
from foundation_harness.config import digest
from scripts.deployment_target import DeploymentTarget
from scripts import journey_platform as journey

TAGS = {"project": "governed-agent-builder", "journey": "create-agent", "auto-delete": "no"}
NO_RETRIES = Config(retries={"total_max_attempts": 1, "mode": "standard"}, connect_timeout=5, read_timeout=60)


class PendingOperation(RuntimeError):
    """Acceptance is unknown; reads are allowed but another write is not."""

def json_file(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Invalid JSON constant")))


class PlatformTarget(DeploymentTarget):
    operations_key = "platformOperations"

    def save(self, key, value):
        """Merge unrelated state updates; atomically fsync intents before dispatch."""
        before = self.path.read_bytes()
        latest = json_file(self.path)
        if latest.get("target") != self.binding:
            raise RuntimeError("Deployment state target changed")
        if latest.get(key) != self.state.get(key):
            raise RuntimeError("Concurrent deployment state change; reconcile before continuing")
        latest[key] = copy.deepcopy(value)
        fd, temporary = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as output:
                json.dump(latest, output, indent=2, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            if self.path.read_bytes() != before:
                raise RuntimeError("Concurrent deployment state change; reconcile before continuing")
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self.state = latest
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class Journal:
    def __init__(self, target):
        self.target = target
        self.key = getattr(target, "operations_key", "platformOperations")

    def save(self, name, record):
        operations = copy.deepcopy(self.target.state.get(self.key, {}))
        operations[name] = record
        self.target.save(self.key, operations)

    def run(self, name, request, write, reconcile):
        """One dispatch, including after interruption between intent and dispatch."""
        request = copy.deepcopy(request)
        request_digest = digest([self.target.binding, request])
        record = copy.deepcopy(self.target.state.get(self.key, {}).get(name))
        if record:
            if record["request_digest"] != request_digest or record["request"] != request:
                raise RuntimeError("Recorded operation request changed: " + name)
            if record["status"] == "COMPLETE":
                return copy.deepcopy(record["result"])
        else:
            record = {"request": request, "request_digest": request_digest,
                      "request_token": digest([self.target.binding, name, request]),
                      "status": "INTENT", "time": time.time()}
            self.save(name, record)
            try:
                result = write(record["request_token"])
            except Exception:
                # SDK error messages can contain response bodies; retain no raw error.
                record["status"] = "UNCERTAIN"
                self.save(name, record)
            else:
                record.update(status="COMPLETE", result=result, completed_at=time.time())
                self.save(name, record)
                return copy.deepcopy(result)
        result = reconcile(record)
        if result is None:
            raise PendingOperation(name + ": acceptance unresolved; GET-only recovery, no automatic replay")
        record.update(status="COMPLETE", result=result, completed_at=time.time())
        self.save(name, record)
        return copy.deepcopy(result)


def missing(read):
    try:
        return read()
    except ClientError as exc:
        error = exc.response["Error"]
        if (error["Code"] in {"ResourceNotFoundException", "NoSuchKey", "NotFound", "404"}
                or (error["Code"] == "ValidationError" and "does not exist" in error.get("Message", ""))):
            return None
        raise


def listed(method, **kwargs):
    found, tokens = [], set()
    for _ in range(20):
        page = method(**kwargs)
        found.extend(page["items"])
        token = page.get("nextToken")
        if not token:
            return found
        if token in tokens:
            break
        tokens.add(token)
        kwargs["nextToken"] = token
    raise RuntimeError("Incomplete resource listing; refusing a partial ownership check")


def arn_in_target(arn, target, service, resource_prefix):
    prefix = f"arn:aws:{service}:{target.binding['region']}:{target.binding['account']}:{resource_prefix}"
    if not isinstance(arn, str) or not arn.startswith(prefix) or not arn[len(prefix):]:
        raise RuntimeError("Resource ARN target/identity mismatch")


def require_tags(control, arn):
    tags = control.list_tags_for_resource(resourceArn=arn)["tags"]
    if any(tags.get(key) != value for key, value in TAGS.items()):
        raise RuntimeError("Unowned resource or missing mandatory tags")

class UploadClient:
    """Keep journey.upload's packaging/transfer flow, with durable per-object writes."""
    def __init__(self, client, target):
        self.client, self.target = client, target

    def __getattr__(self, name):
        return getattr(self.client, name)

    def upload_file(self, filename, bucket, key, *, ExtraArgs, Config):
        path = Path(filename)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        request = {"bucket": bucket, "key": key, "sha256": sha, "size": path.stat().st_size,
                   "extra_args": ExtraArgs}
        name = "upload-" + digest([bucket, key])
        def read(record):
            head = missing(lambda: self.client.head_object(Bucket=bucket, Key=key))
            if head is None:
                return None
            if (head.get("Metadata") != {"sha256": sha, "request-token": record["request_token"]}
                    or head["ContentLength"] != request["size"]
                    or head.get("ServerSideEncryption") != "AES256"
                    or head.get("VersionId") in (None, "null")):
                raise RuntimeError("Upload receipt does not match the intended versioned object")
            return {"version_id": head["VersionId"], "sha256": sha}
        if name not in self.target.state.get(getattr(self.target, "operations_key", "platformOperations"), {}):
            head = missing(lambda: self.client.head_object(Bucket=bucket, Key=key))
            if head is not None:
                # The base deployment already uploaded its Lambda ZIP. Reuse
                # only that bound release, verifying actual versioned bytes.
                state = self.target.state
                if (bucket != state.get("artifacts", {}).get("outputs", {}).get("Bucket")
                        or state.get("releaseSha256") != sha or key != f"releases/{sha}/lambda.zip"
                        or head.get("VersionId") in (None, "null") or head.get("ContentLength") != request["size"]
                        or head.get("ServerSideEncryption") != "AES256"):
                    raise RuntimeError("Unrecorded release object exists; operator reconciliation required")
                response = self.client.get_object(Bucket=bucket, Key=key, VersionId=head["VersionId"])
                actual = hashlib.sha256()
                with response["Body"] as body:
                    for chunk in iter(lambda: body.read(1024 * 1024), b""):
                        actual.update(chunk)
                if actual.hexdigest() != sha:
                    raise RuntimeError("Base release content does not match its recorded digest")
                return
        def write(token):
            self.client.upload_file(filename, bucket, key, Config=Config,
                                    ExtraArgs={**ExtraArgs, "Metadata": {"sha256": sha, "request-token": token}})
            result = read({"request_token": token})
            if result is None:
                raise PendingOperation("Upload is not yet readable")
            return result
        result = Journal(self.target).run(name, request, write, read)
        record = self.target.state[getattr(self.target, "operations_key", "platformOperations")][name]
        if read(record) != result:
            raise RuntimeError("Recorded release object changed")


class NoRetrySession:
    def __init__(self, session, target):
        self.session, self.target = session, target

    def __getattr__(self, name):
        return getattr(self.session, name)

    def client(self, service, **kwargs):
        kwargs["config"] = kwargs.get("config", Config()).merge(NO_RETRIES)
        client = self.session.client(service, **kwargs)
        return UploadClient(client, self.target) if service == "s3" else client

    def resource(self, service, **kwargs):
        kwargs["config"] = kwargs.get("config", Config()).merge(NO_RETRIES)
        return self.session.resource(service, **kwargs)


def stack(target, bucket, key):
    """Use the shared Journey role template; add request receipts around deployment."""
    body = journey.platform_template()
    request = {"StackName": journey.STACK, "TemplateBody": json.dumps(body),
               "Capabilities": ["CAPABILITY_IAM"],
               "Tags": [{"Key": k, "Value": v} for k, v in TAGS.items()],
               "Parameters": [{"ParameterKey": "ArtifactBucket", "ParameterValue": bucket},
                              {"ParameterKey": "ArtifactKey", "ParameterValue": key}]}
    cf, saved = target.cf, target.state.get("journeyStack")

    def read():
        current = missing(lambda: cf.describe_stacks(StackName=journey.STACK)["Stacks"][0])
        if current is None:
            return None
        arn_in_target(current["StackId"], target, "cloudformation", "stack/" + journey.STACK + "/")
        if saved and current["StackId"] != saved["id"]:
            raise RuntimeError("Journey stack identity changed")
        tags = {t["Key"]: t["Value"] for t in current.get("Tags", [])}
        if any(tags.get(k) != v for k, v in TAGS.items()):
            raise RuntimeError("Unowned Journey stack")
        template = cf.get_template(StackName=current["StackId"])["TemplateBody"]
        template = json.loads(template) if isinstance(template, str) else template
        params = {p["ParameterKey"]: p.get("ParameterValue") for p in current.get("Parameters", [])}
        if template != body or params != {"ArtifactBucket": bucket, "ArtifactKey": key}:
            raise RuntimeError("Journey stack contract changed; no automatic update")
        return current

    recorded = target.state.get(getattr(target, "operations_key", "platformOperations"), {}).get("stack")
    current = read()
    if current and not saved and not recorded:
        raise RuntimeError("Refusing to adopt an unrecorded Journey stack")
    if not current and saved:
        raise RuntimeError("Saved Journey stack is missing")
    def reconcile(record):
        current = read()
        return {"id": current["StackId"]} if current else None
    if not saved:
        def create(token):
            cf.validate_template(TemplateBody=request["TemplateBody"])
            result = cf.create_stack(**request, ClientRequestToken=token)
            return {"id": result["StackId"]}
        receipt = Journal(target).run("stack", request, create, reconcile)
        current = read()
        if current is None or current["StackId"] != receipt["id"]:
            raise PendingOperation("Created Journey stack is not yet readable")
    result = journey.wait(read)
    outputs = {o["OutputKey"]: o["OutputValue"] for o in result["Outputs"]}
    if saved and saved["outputs"] != outputs:
        raise RuntimeError("Journey stack outputs changed")
    for field in ("GatewayRole", "RuntimeRole", "KnowledgeFunction"):
        if field not in outputs:
            continue
        parts = outputs[field].split(":")
        if parts[4] != target.binding["account"] or parts[3] not in ("", target.binding["region"]):
            raise RuntimeError("Journey stack output target mismatch")
    target.save("journeyStack", {"id": result["StackId"], "outputs": outputs})
    return outputs


def gateway_identity(target, control, gw):
    """Tag only the identity that AWS binds to this exact owned Gateway."""
    arn_in_target(gw["arn"], target, "bedrock-agentcore", "gateway/gab-journey-tools-")
    require_tags(control, gw["arn"])
    current = control.get_gateway(gatewayIdentifier=gw["id"])
    arn = (f"arn:aws:bedrock-agentcore:{target.binding['region']}:{target.binding['account']}:"
           f"workload-identity-directory/default/workload-identity/{gw['id']}")
    if (current["gatewayArn"] != gw["arn"] or current["gatewayId"] != gw["id"]
            or current.get("workloadIdentityDetails", {}).get("workloadIdentityArn") != arn):
        raise RuntimeError("Gateway workload identity binding changed")
    request = {"resourceArn": arn, "tags": TAGS}

    def read(record=None):
        tags = control.list_tags_for_resource(resourceArn=arn)["tags"]
        if any(key in tags and tags[key] != TAGS[key] for key in ("project", "journey")):
            raise RuntimeError("Gateway workload identity owner changed")
        return {"arn": arn} if all(tags.get(key) == value for key, value in TAGS.items()) else None

    observed = read()
    recorded = target.state.get(getattr(target, "operations_key", "platformOperations"), {}).get("gateway-identity-tags")
    if observed is None or (recorded and recorded["status"] != "COMPLETE"):
        def write(token):
            control.tag_resource(**request)
            return {"arn": arn}
        Journal(target).run("gateway-identity-tags", request, write, read)
    result = read()
    if result is None:
        raise RuntimeError("Gateway workload identity retention tags are missing")
    target.save("journeyGatewayIdentity", result)
    return result

def gateway(target, control, outputs):
    saved = target.state.get("journeyGateway")
    request = {"name": "gab-journey-tools", "roleArn": outputs["GatewayRole"], "authorizerType": "AWS_IAM",
               "protocolType": "MCP", "protocolConfiguration": {"mcp": {"supportedVersions": ["2025-03-26"]}},
               "tags": TAGS}

    def find():
        if saved:
            return missing(lambda: control.get_gateway(gatewayIdentifier=saved["id"]))
        matches = [x for x in listed(control.list_gateways) if x["name"] == request["name"]]
        if len(matches) > 1:
            raise RuntimeError("Ambiguous Journey Gateway ownership")
        return control.get_gateway(gatewayIdentifier=matches[0]["gatewayId"]) if matches else None

    def checked(current):
        arn_in_target(current["gatewayArn"], target, "bedrock-agentcore", "gateway/gab-journey-tools-")
        if (current["gatewayArn"].rsplit("/", 1)[-1] != current["gatewayId"]
                or any(current.get(k) != request[k] for k in ("name", "roleArn", "authorizerType", "protocolType"))
                or current.get("protocolConfiguration", {}).get("mcp", {}).get("supportedVersions") != ["2025-03-26"]
                or current.get("authorizerConfiguration") or current.get("interceptorConfigurations")
                or current.get("customTransformConfiguration")):
            raise RuntimeError("Gateway security binding changed")
        require_tags(control, current["gatewayArn"])
        result = {"id": current["gatewayId"], "arn": current["gatewayArn"], "url": current["gatewayUrl"]}
        expected_url = f"https://{result['id']}.gateway.bedrock-agentcore.{target.binding['region']}.amazonaws.com/mcp"
        if result["url"] != expected_url or (saved and any(saved.get(k, v) != v for k, v in result.items())):
            raise RuntimeError("Gateway identity or URL changed")
        return result

    def reconcile(record):
        current = find()
        return checked(current) if current else None

    if saved:
        if find() is None:
            raise RuntimeError("Saved Gateway is missing")
    else:
        if "gateway" not in target.state.get(getattr(target, "operations_key", "platformOperations"), {}) and find() is not None:
            raise RuntimeError("Refusing to adopt an unrecorded Gateway")
        def create(token):
            return checked(control.create_gateway(**request, clientToken=token))
        receipt = Journal(target).run("gateway", request, create, reconcile)
        if reconcile(None) != receipt:
            raise PendingOperation("Created Gateway is not yet readable")
    current = journey.wait(find)
    gw = checked(current)
    target.save("journeyGateway", gw)

    gateway_identity(target, control, gw)
    return gw
