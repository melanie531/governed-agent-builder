"""Complete, immutable MCP archives; uploaded code is never executed by the API."""
import ast
import base64
import hashlib
import json
import math
from pathlib import Path
import re
import stat
import time
from uuid import uuid4
import zipfile
import zlib

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi import HTTPException
from pydantic import Field, model_validator

from foundation_harness.config import digest
from .foundation_runs import get, put
from .journey_schema import Strict

PART_BYTES = 2 * 1024 * 1024
MAX_ZIP = 64 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
MAX_FILES = 20000
SHA = r"^[a-f0-9]{64}$"


class InvalidPackage(ValueError):
    """A deterministic rejection, with no Runtime creation attempted."""


def validate_archive(path, expected_digest):
    path = Path(path)
    if not 0 < path.stat().st_size <= MAX_ZIP or hashlib.sha256(path.read_bytes()).hexdigest() != expected_digest:
        raise InvalidPackage("Package size or checksum is invalid")
    try:
        with zipfile.ZipFile(path) as archive:
            entries, seen, total = archive.infolist(), set(), 0
            if not entries or len(entries) > MAX_FILES:
                raise InvalidPackage("Package has too many entries")
            for entry in entries:
                name = entry.filename
                parts = name.removesuffix("/").split("/")
                kind = stat.S_IFMT(entry.external_attr >> 16)
                if (name in seen or "\0" in entry.orig_filename or "\\" in name or ":" in name
                        or any(p in ("", ".", "..") for p in parts)
                        or kind not in (0, stat.S_IFREG, stat.S_IFDIR)
                        or kind == stat.S_IFDIR and not entry.is_dir()
                        or kind == stat.S_IFREG and entry.is_dir()
                        or entry.flag_bits & 1 or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    raise InvalidPackage("Package contains an unsafe path, duplicate, link or unsupported entry")
                seen.add(name)
                total += entry.file_size
                if total > MAX_EXPANDED:
                    raise InvalidPackage("Expanded package exceeds 256 MiB")
                if entry.is_dir():
                    continue
                if any("/".join(parts[:i]) in seen for i in range(1, len(parts))):
                    raise InvalidPackage("Package contains conflicting file paths")
                with archive.open(entry) as reader:
                    head, actual = reader.read(1024 * 1024), 0
                    if name == "main.py":
                        if entry.file_size > 1024 * 1024:
                            raise InvalidPackage("main.py exceeds 1 MiB")
                        ast.parse(head.decode("utf-8"), filename="main.py")
                    if name.endswith((".so", ".dylib", ".dll", ".pyd")):
                        if not (head[:6] == b"\x7fELF\x02\x01" and len(head) >= 20
                                and int.from_bytes(head[18:20], "little") == 183):
                            raise InvalidPackage("Native libraries must target Linux ARM64")
                    block = head
                    while block:
                        actual += len(block)
                        if actual > entry.file_size or actual > MAX_EXPANDED:
                            raise InvalidPackage("Package entry size changed")
                        block = reader.read(1024 * 1024)
                    if actual != entry.file_size:
                        raise InvalidPackage("Package entry is truncated")
            if "main.py" not in seen:
                raise InvalidPackage("Put main.py at the ZIP root, without a parent folder")
            files = {e.filename for e in entries if not e.is_dir()}
            if any(any("/".join(n.split("/")[:i]) in files for i in range(1, len(n.split("/"))))
                   for n in seen):
                raise InvalidPackage("Package contains conflicting file paths")
            contract = {}
            if "mcp-package.json" in files:
                if archive.getinfo("mcp-package.json").file_size > 4096:
                    raise InvalidPackage("Package manifest exceeds 4 KiB")
                manifest = json.loads(archive.read("mcp-package.json"))
                if (not isinstance(manifest, dict)
                        or set(manifest) != {"schema_version", "bearer_validation_tool"}
                        or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
                        or not isinstance(manifest["bearer_validation_tool"], str)
                        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", manifest["bearer_validation_tool"])):
                    raise InvalidPackage("Invalid package authentication manifest")
                contract["bearer_validation_tool"] = manifest["bearer_validation_tool"]
            return {"files": len(files), "expanded_bytes": total, **contract}
    except InvalidPackage:
        raise
    except (zipfile.BadZipFile, UnicodeError, SyntaxError, ValueError, RuntimeError, EOFError, OSError, zlib.error):
        raise InvalidPackage("ZIP integrity or main.py syntax is invalid") from None


class PackageInput(Strict):
    name: str = Field(min_length=2, max_length=80)
    filename: str = Field(pattern=r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}\.zip$")
    size: int = Field(gt=0, le=MAX_ZIP, strict=True)
    source_digest: str = Field(pattern=SHA)
    # Old retained requests keep their original contract. New clients send only
    # generic archive metadata; authentication belongs to the later connection.
    connection_mode: str = Field(default="PACKAGE", pattern=r"^(PACKAGE|IAM|SNOWFLAKE_OAUTH)$")
    snowflake_account: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,100}$")
    snowflake_role: str | None = Field(default=None, pattern=r"^[A-Z_][A-Z0-9_]{0,254}$")
    warehouse: str | None = Field(default=None, pattern=r"^[A-Z_][A-Z0-9_]{0,254}$")
    idempotency_key: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")

    @model_validator(mode="after")
    def mode_fields(self):
        fields = (self.snowflake_account, self.snowflake_role, self.warehouse)
        if (len(self.name.strip()) < 2
                or self.connection_mode != "SNOWFLAKE_OAUTH" and any(fields)
                or self.connection_mode == "SNOWFLAKE_OAUTH" and (not all(fields)
                    or self.snowflake_role in {"ACCOUNTADMIN", "SECURITYADMIN", "USERADMIN", "SYSADMIN", "ORGADMIN"})):
            raise ValueError("Use the settings for the selected connection mode")
        return self


class PartInput(Strict):
    data: str = Field(min_length=1, max_length=4 * math.ceil(PART_BYTES / 3))
    digest: str = Field(pattern=SHA)
    retry: bool = False


class PackageCloud:
    def __init__(self, settings, session=None):
        self.settings = settings
        self.s3 = (session or boto3.Session(region_name=settings["region"])).client("s3", config=Config(
            retries={"total_max_attempts": 1}, connect_timeout=5, read_timeout=30))

    @staticmethod
    def part_key(sid, index):
        return f"mcp/python/uploads/{sid}/part-{index:04d}"

    def read_part(self, state, index, part, config):
        bucket, key = config["bucket"], self.part_key(state["id"], index)
        try:
            value = self.s3.head_object(Bucket=bucket, Key=key, ChecksumMode="ENABLED",
                                       ExpectedBucketOwner=self.settings["account"])
        except ClientError as error:
            if error.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return None
            if error.response["Error"]["Code"] in ("403", "AccessDenied"):
                page = self.s3.list_objects_v2(Bucket=bucket, Prefix=key, MaxKeys=1,
                                              ExpectedBucketOwner=self.settings["account"])
                if not any(i["Key"] == key for i in page.get("Contents", [])):
                    return None
            raise
        expected = {"upload-id": state["id"], "archive-digest": state["source_digest"], "sha256": part["digest"]}
        if (value.get("Metadata") != expected or value.get("ContentLength") != part["size"]
                or value.get("ChecksumSHA256") != base64.b64encode(bytes.fromhex(part["digest"])).decode()
                or value.get("ServerSideEncryption") != "AES256"
                or not value.get("VersionId") or value["VersionId"] == "null"):
            raise ValueError("Uploaded package part binding changed")
        return {"key": key, "version_id": value["VersionId"], "digest": part["digest"], "size": part["size"]}

    def write_part(self, state, index, data, part, config):
        return self.s3.put_object(Bucket=config["bucket"], Key=self.part_key(state["id"], index), Body=data,
            ExpectedBucketOwner=self.settings["account"], IfNoneMatch="*", ServerSideEncryption="AES256",
            ChecksumAlgorithm="SHA256", ChecksumSHA256=base64.b64encode(bytes.fromhex(part["digest"])).decode(),
            ContentType="application/octet-stream",
            Metadata={"upload-id": state["id"], "archive-digest": state["source_digest"], "sha256": part["digest"]},
            Tagging="auto-delete=no")


class PackageUploads:
    def __init__(self, python, cloud=None):
        self.python, self.service = python, python.service
        self.cloud = cloud or PackageCloud(self.service.settings)

    def config(self, db):
        from .mcp_python_cloud import artifact_bucket
        config = self.python.config(db)
        binding = self.service.settings.get("mcp_package_upload", {})
        if (binding.get("bucket") != artifact_bucket(config)
                or binding.get("runtime_prefix") != config["runtime_prefix"]):
            raise HTTPException(409, "Complete MCP package uploads are not configured")
        return {"bucket": binding["bucket"], "python": config}

    def load(self, db, actor, sid):
        state = get(db, "mcp-package:" + sid)
        if not state or state["requester"] != actor["id"]:
            raise HTTPException(404, "Package upload not found")
        return state

    def writable(self, db, actor, sid):
        state, config = self.load(db, actor, sid), self.config(db)
        if (state.get("job_id") or state.get("phase") == "DELETED"
                or state["expires"] < time.time() or digest(config) != state["config_digest"]):
            raise HTTPException(409, "Package upload is finalized, expired or its deployment settings changed")
        return state, config

    @staticmethod
    def public(state, received=()):
        return {**{k: state[k] for k in ("id", "name", "filename", "source_digest", "size", "connection_mode", "created")},
                "phase": "UPLOADING", "part_bytes": PART_BYTES, "part_count": math.ceil(state["size"] / PART_BYTES),
                "received_parts": list(received)}

    def reserve(self, actor, value):
        self.service.admin(actor)
        try:
            body = PackageInput.model_validate(value)
        except Exception:
            raise HTTPException(422, "Choose a complete ZIP of at most 64 MiB with a name and valid checksum") from None
        metadata = body.model_dump(exclude={"idempotency_key"}, exclude_none=True)
        def save(db):
            config = self.config(db)
            if body.connection_mode == "SNOWFLAKE_OAUTH" and not config["python"].get("facade_url"):
                raise HTTPException(422, "The Snowflake OAuth bridge is not configured")
            key = "mcp-package-request:" + digest([actor["id"], body.idempotency_key])
            prior = get(db, key)
            if prior:
                state = self.load(db, actor, prior["id"])
                if state["request_digest"] != digest(metadata):
                    raise HTTPException(409, "The retained package request has different contents or settings")
                return self.public(state) if not state.get("job_id") else self.python.public(self.python.load(db, state["id"]))
            from .mcp_deployments import active_ids
            ids = active_ids(db)
            if len(ids) >= 20:
                raise HTTPException(429, "Python MCP runtime limit reached")
            sid, now = uuid4().hex, time.time()
            state = {**metadata, "id": sid, "requester": actor["id"], "created": now, "expires": now + 86400,
                     "parts": {}, "config_digest": digest(config), "request_digest": digest(metadata)}
            put(db, "mcp-package:" + sid, state)
            put(db, key, {"id": sid})
            self.service.audit(db, actor["id"], "mcp_package_upload_requested", sid,
                               {"source_digest": body.source_digest, "size": body.size})
            return self.public(state)
        return self.service.tx(save)

    def detail(self, actor, sid):
        self.service.admin(actor)
        state, config = self.service.tx(lambda db: (self.load(db, actor, sid), self.config(db)))
        if state.get("job_id"):
            return self.python.detail(actor, sid)
        received = [int(i) for i, part in state["parts"].items()
                    if part.get("receipt") or self.cloud.read_part(state, int(i), part, config)]
        return self.public(state, sorted(received))

    def request(self, actor, token):
        self.service.admin(actor)
        def read(db):
            prior = get(db, "mcp-package-request:" + digest([actor["id"], token]))
            if not prior:
                raise HTTPException(404, "Package request not found")
            return prior["id"]
        return self.detail(actor, self.service.tx(read))

    def part(self, actor, sid, index, value):
        self.service.admin(actor)
        try:
            body = PartInput.model_validate(value)
            data = base64.b64decode(body.data, validate=True)
            if not data or len(data) > PART_BYTES or hashlib.sha256(data).hexdigest() != body.digest:
                raise ValueError()
        except Exception:
            raise HTTPException(422, "Package part checksum or size is invalid") from None
        token = uuid4().hex
        def reserve(db):
            state, config = self.writable(db, actor, sid)
            if (not 0 <= index < math.ceil(state["size"] / PART_BYTES)
                    or len(data) != min(PART_BYTES, state["size"] - index * PART_BYTES)):
                raise HTTPException(422, "Package part does not match the reserved archive size")
            previous = state["parts"].get(str(index))
            if previous and (previous["digest"] != body.digest or previous.get("claim_expires", 0) > time.time()):
                raise HTTPException(409, "The package part changed or is still uploading; check its status")
            part = {"digest": body.digest, "size": len(data), "claim": token, "claim_expires": time.time() + 120}
            state["parts"][str(index)] = part
            put(db, "mcp-package:" + sid, state)
            return state, config, not previous or body.retry
        state, config, may_write = self.service.tx(reserve)
        part = state["parts"][str(index)]
        receipt = None
        try:
            receipt = self.cloud.read_part(state, index, part, config)
            if receipt is None:
                if not may_write:
                    raise ValueError("Explicit retry is required")
                self.cloud.write_part(state, index, data, part, config)
                receipt = self.cloud.read_part(state, index, part, config)
            if receipt is None:
                raise ValueError("Package part is not confirmed")
        except Exception:
            raise HTTPException(409, "Upload outcome needs checking. Check package status, then explicitly resume the same ZIP") from None
        finally:
            def release(db):
                current = self.load(db, actor, sid)
                if current["parts"][str(index)].get("claim") == token:
                    current["parts"][str(index)].update(claim=None, claim_expires=0)
                    if receipt:
                        current["parts"][str(index)]["receipt"] = receipt
                    put(db, "mcp-package:" + sid, current)
            self.service.tx(release)
        return self.detail(actor, sid)

    def deploy(self, actor, sid, session_hash=None):
        self.service.admin(actor)
        state = self.service.tx(lambda db: self.load(db, actor, sid))
        if state.get("job_id"):
            return self.python.detail(actor, sid)
        state, config = self.service.tx(lambda db: self.writable(db, actor, sid))
        count = math.ceil(state["size"] / PART_BYTES)
        if (len(state["parts"]) != count
                or any(p.get("claim_expires", 0) > time.time() for p in state["parts"].values())):
            raise HTTPException(409, "Finish uploading the complete package before deployment")
        parts = [self.cloud.read_part(state, i, state["parts"][str(i)], config) for i in range(count)]
        if not all(parts):
            raise HTTPException(409, "Some package parts are not confirmed; resume the same ZIP")
        def finalize(db):
            current = self.load(db, actor, sid)
            if current.get("job_id"):
                return self.python.public(self.python.load(db, sid))
            current, binding = self.writable(db, actor, sid)
            if digest(current) != digest(state):
                raise HTTPException(409, "Package upload changed; check its status")
            native = binding["python"]
            runtime = {**{k: v for k, v in state.items() if k not in ("parts", "expires", "config_digest")},
                "upload_type": "package", "source_parts": parts, "phase": "PACKAGING", "stage": "package",
                "operations": {}, "config_digest": digest(native), "runtime_name": native["runtime_prefix"] + "_" + sid[:24],
                "endpoint": native["facade_url"] + "/mcp/" + sid if state["connection_mode"] == "SNOWFLAKE_OAUTH" else ""}
            result = self.python.job(db, runtime, actor, session_hash)
            current["job_id"] = result["job_id"]
            put(db, "mcp-package:" + sid, current)
            self.service.audit(db, actor["id"], "mcp_package_deployment_requested", sid,
                               {"source_digest": state["source_digest"], "parts": count})
            return result
        return self.service.tx(finalize)
