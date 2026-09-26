"""Administrator management of Studio-owned, unused credential connections."""
import json
import re
import time
from uuid import uuid4

from fastapi import HTTPException
from pydantic import Field, SecretStr

from foundation_harness.config import digest
from .foundation_runs import get, put
from .mcp_credentials import CredentialInput, failure_code, failure_context
from .mcp_management import DeleteConnection


class EditCredential(CredentialInput):
    expected_revision: int = Field(ge=1)
    secret: SecretStr = SecretStr("")


def native_state(db, cid):
    for row in db.select("settings"):
        if row["key"].startswith("mcp-auth-request:"):
            state = json.loads(row["body"])
            if state.get("connection_id") == cid:
                return row["key"], state
    return None, None


def references(db, cid):
    result = []
    for row in db.select("settings"):
        if row["key"].startswith("mcp-connection:"):
            state = json.loads(row["body"])
            if state["phase"] != "DELETED" and cid in (
                    state["connection_id"], state.get("change", {}).get("payload", {}).get("connection_id")):
                result.append({"id": state["id"], "name": state["name"], "phase": state["phase"]})
    return result


def unlocked(db, cid):
    if get(db, "mcp-auth-lock:" + cid):
        raise HTTPException(409, "Authentication connection is being changed; finish that operation first")


class AuthManagement:
    def __init__(self, credentials):
        self.credentials, self.service, self.cloud = credentials, credentials.service, credentials.cloud

    def detail_db(self, db, cid):
        connection = next((c for c in self.service.config(db)["connections"] if c["id"] == cid), None)
        if not connection:
            raise HTTPException(404, "Authentication connection not found")
        _, native = native_state(db, cid)
        refs = references(db, cid)
        lock = get(db, "mcp-auth-lock:" + cid)
        managed = bool(native and native["phase"] in ("READY", "MANAGING"))
        editable = managed and not refs and not lock
        provider = connection["configuration"].get("credentialProvider", {}).get("apiKeyCredentialProvider", {})
        return {"id": cid, "name": connection["name"], "allowed_origins": connection["allowed_origins"],
                "auth_type": connection["configuration"]["credentialProviderType"],
                "header": provider.get("credentialParameterName", ""), "prefix": provider.get("credentialPrefix", ""),
                "revision": (native or {}).get("revision", 1), "managed": managed, "references": refs,
                "phase": "CHANGING" if lock else "READY", "operation": lock,
                "can_edit": editable, "can_delete": editable,
                "reason": ("Managed by the deployment. Add a separate authentication connection to use your own credential."
                           if not managed else "Used by MCP connections. Change or delete those connections first." if refs else
                           "Finish the retained authentication operation first." if lock else "")}

    def detail(self, actor, cid):
        self.service.admin(actor)
        return self.service.tx(lambda db: self.detail_db(db, cid))

    def list(self, actor):
        self.service.admin(actor)
        return self.service.tx(lambda db: {"items": [
            self.detail_db(db, c["id"]) for c in self.service.config(db)["connections"]]})

    def key(self, actor, cid, token):
        return "mcp-auth-change:" + digest([actor["id"], cid, token])

    @staticmethod
    def public(op):
        return {k: op[k] for k in ("phase", "connection_id", "token", "stage", "failure_code", "failure_context",
                                   "can_continue", "retry_count") if k in op}

    def change(self, actor, cid, value, kind):
        from .mcp_onboarding import endpoint_origin
        self.service.admin(actor)
        try:
            body = (EditCredential if kind == "edit" else DeleteConnection).model_validate(value)
            secret = body.secret.get_secret_value() if kind == "edit" else ""
            if kind == "edit":
                origin = endpoint_origin(body.endpoint)
                if (len(secret) > 8192 or re.search(r"[\x00-\x1f\x7f]", secret)
                        or body.header.lower() in {"host", "cookie", "content-length", "transfer-encoding", "connection"}):
                    raise ValueError()
        except Exception:
            raise HTTPException(422, "Enter valid connection details and an API key or PAT, or leave the secret empty to keep it") from None
        metadata = body.model_dump(exclude={"secret"})
        key = self.key(actor, cid, body.idempotency_key)
        def reserve(db):
            prior = get(db, key)
            if prior:
                if prior["digest"] != digest([kind, metadata, bool(secret)]):
                    raise HTTPException(409, "This request is already retained with different details")
                return prior, False
            info = self.detail_db(db, cid)
            if not info["can_edit"] or info["revision"] != body.expected_revision:
                raise HTTPException(409, info["reason"] or "Authentication connection changed; refresh it")
            if kind == "delete" and body.confirm_name != info["name"]:
                raise HTTPException(422, "Enter the exact authentication connection name")
            native_key, native = native_state(db, cid)
            op = {"connection_id": cid, "token": body.idempotency_key, "kind": kind,
                  "native_key": native_key, "native": native, "phase": "CHANGING", "operations": {},
                  "version": uuid4().hex, "retry_count": 0, "created": time.time(),
                  "digest": digest([kind, metadata, bool(secret)]),
                  "stages": (["rotate"] if secret else []) if kind == "edit" else ["provider_delete", "secret_delete"]}
            if kind == "edit":
                op["metadata"] = {"name": body.name.strip(), "origin": origin, "header": body.header, "prefix": body.prefix.strip()}
            put(db, key, op)
            put(db, "mcp-auth-lock:" + cid, {"key": key, "token": body.idempotency_key, "owner": actor["id"]})
            put(db, native_key, {**native, "phase": "MANAGING"})
            self.service.audit(db, actor["id"], "mcp_auth_" + kind + "_requested", cid,
                               {"revision": body.expected_revision, "request": body.idempotency_key})
            return op, True
        op, fresh = self.service.tx(reserve)
        return self.run(actor, key, dispatch=fresh, secret=secret)

    def run(self, actor, key, *, dispatch=False, secret="", retry=False):
        self.service.admin(actor)
        op = self.service.tx(lambda db: get(db, key))
        if not op:
            raise HTTPException(404, "Authentication operation not found")
        if op["phase"] in ("READY", "DELETED"):
            return self.public(op)
        def save(action):
            def update(db):
                current = get(db, key)
                lock = get(db, "mcp-auth-lock:" + op["connection_id"])
                if not lock or lock["key"] != key or lock["owner"] != actor["id"]:
                    raise HTTPException(409, "Authentication operation changed")
                action(current)
                put(db, key, current)
                return current
            return self.service.tx(update)
        try:
            for stage in op["stages"]:
                operation = op["operations"].get(stage)
                if operation == "COMPLETE":
                    continue
                done = self.cloud.management_read(stage, op["native"], op)
                if not done:
                    if not dispatch or (operation and not retry) or (stage == "rotate" and not secret):
                        op = save(lambda current: current.update(
                            stage=stage, phase="NEEDS_RECONCILIATION" if operation else "CONTINUE",
                            can_continue=not operation or current.get("retry_count", 0) < 3))
                        return self.public(op)
                    def intent(current):
                        if current["operations"].get(stage) != operation:
                            raise HTTPException(409, "Authentication dispatch changed")
                        if operation:
                            if current["retry_count"] >= 3:
                                raise HTTPException(409, "Authentication retry limit reached")
                            current["retry_count"] += 1
                        current["operations"][stage] = "INTENT"
                    op = save(intent)
                    self.cloud.management_write(stage, op["native"], op, secret)
                    op = save(lambda current: current["operations"].update({stage: "ACKNOWLEDGED"}))
                    done = self.cloud.management_read(stage, op["native"], op)
                if not done:
                    op = save(lambda current: current.update(stage=stage, phase="CONTINUE", can_continue=True))
                    return self.public(op)
                op = save(lambda current: current["operations"].update({stage: "COMPLETE"}))
                retry = False
            def finish(db):
                current = get(db, key)
                lock = get(db, "mcp-auth-lock:" + op["connection_id"])
                if current["phase"] in ("READY", "DELETED"):
                    return current
                if not lock or lock["key"] != key:
                    raise HTTPException(409, "Authentication operation changed")
                native = get(db, op["native_key"])
                native["revision"] = native.get("revision", 1) + 1
                if op["kind"] == "delete":
                    db.delete("settings", where=[("key", "=", "mcp-auth:" + op["connection_id"])])
                    native["phase"] = current["phase"] = "DELETED"
                else:
                    native.update(**op["metadata"], phase="READY")
                    if "rotate" in op["stages"]:
                        native["secret_version"] = op["version"]
                    connection = get(db, "mcp-auth:" + op["connection_id"])
                    connection.update(name=native["name"], allowed_origins=[native["origin"]])
                    connection["configuration"]["credentialProvider"]["apiKeyCredentialProvider"].update(
                        credentialParameterName=native["header"], credentialPrefix=native["prefix"])
                    put(db, "mcp-auth:" + op["connection_id"], connection)
                    current["phase"] = "READY"
                current.pop("failure_code", None)
                current.pop("failure_context", None)
                current["can_continue"] = False
                put(db, op["native_key"], native)
                put(db, key, current)
                db.delete("settings", where=[("key", "=", "mcp-auth-lock:" + op["connection_id"])])
                self.service.audit(db, actor["id"], "mcp_auth_" + op["kind"] + "_completed", op["connection_id"],
                                   {"revision": native["revision"]})
                return current
            return self.public(self.service.tx(finish))
        except HTTPException:
            raise
        except Exception as error:
            op = save(lambda current: current.update(phase="NEEDS_RECONCILIATION", can_continue=True,
                                                     failure_code=failure_code(error), failure_context=failure_context(error)))
            return self.public(op)

    def status(self, actor, cid, token):
        return self.run(actor, self.key(actor, cid, token))

    def resume(self, actor, cid, token, value):
        try:
            secret = value.get("secret", "")
            retry = value.get("retry", False)
            if not isinstance(secret, str) or len(secret) > 8192 or re.search(r"[\x00-\x1f\x7f]", secret) or not isinstance(retry, bool):
                raise ValueError()
        except Exception:
            raise HTTPException(422, "Invalid authentication continuation") from None
        return self.run(actor, self.key(actor, cid, token), dispatch=True, secret=secret, retry=retry)
