"""Requests for new tools; this workflow never grants existing Catalog access."""
import json
import time
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, model_validator

from foundation_harness.config import digest
from .foundation_runs import get, put
from .journey_schema import Strict


class NewToolRequest(Strict):
    title: str = Field(min_length=1, max_length=160)
    details: str = Field(default="", max_length=4000)
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")

    @model_validator(mode="after")
    def normalize(self):
        self.title, self.details = self.title.strip(), self.details.strip()
        if not self.title:
            raise ValueError("Enter the name of the tool you need")
        return self


class ToolResponse(Strict):
    status: Literal["IN_REVIEW", "FULFILLED", "DECLINED"]
    response: str = Field(min_length=5, max_length=2000)
    version: int = Field(ge=1)

    @model_validator(mode="after")
    def meaningful_response(self):
        self.response = self.response.strip()
        if len(self.response) < 5:
            raise ValueError("Provide a response of at least five characters")
        return self


def router(store, who):
    routes = APIRouter(prefix="/api")

    def transaction(operation):
        for attempt in range(6):
            try:
                with store.tx() as db:
                    return operation(db)
            except HTTPException as exc:
                if exc.status_code != 409 or exc.detail != "Concurrent governance update; reload and retry" or attempt == 5:
                    raise
                time.sleep(0.025 * (attempt + 1))

    def records(db):
        return [json.loads(row["body"]) for row in db.select("settings") if row["key"].startswith("tool-request:")]

    @routes.get("/tool-requests")
    def listing(request: Request):
        actor = who(request)
        return transaction(lambda db: sorted(
            [row for row in records(db) if actor["role"] == "admin" or
             (row["requester"] == actor["id"] and row["workspace"] == actor["workspace"])],
            key=lambda row: row["created"], reverse=True))

    @routes.post("/tool-requests", status_code=201)
    def create(body: NewToolRequest, request: Request):
        actor = who(request)
        if actor["role"] != "business":
            raise HTTPException(403, "Business workspace membership required")

        def save(db):
            key = "tool-request-idempotency:" + digest([actor["id"], body.idempotency_key])
            previous = get(db, key)
            signature = digest([body.title, body.details, actor["workspace"]])
            if previous:
                if previous["signature"] != signature:
                    raise HTTPException(409, "Request key was already used for different content")
                return get(db, "tool-request:" + previous["id"])
            if sum(row["requester"] == actor["id"] and row["created"] > time.time() - 3600 for row in records(db)) >= 30:
                raise HTTPException(429, "Tool request limit reached; try again later")
            row = {"id": uuid4().hex, "requester": actor["id"], "requester_name": actor["name"],
                   "workspace": actor["workspace"], "title": body.title, "details": body.details,
                   "status": "SUBMITTED", "response": "", "version": 1, "created": time.time()}
            put(db, "tool-request:" + row["id"], row)
            put(db, key, {"id": row["id"], "signature": signature})
            db.insert("audit", {"actor": actor["id"], "action": "tool_requested", "resource": row["id"],
                               "detail": json.dumps({"title": row["title"], "workspace": row["workspace"]}), "created": time.time()})
            return row
        return transaction(save)

    @routes.post("/admin/tool-requests/{request_id}/response")
    def respond(request_id: str, body: ToolResponse, request: Request):
        actor = who(request, True)

        def save(db):
            key = "tool-request:" + request_id
            row = get(db, key)
            if not row:
                raise HTTPException(404, "Tool request not found")
            if row["version"] != body.version:
                raise HTTPException(409, "This request changed; refresh before responding")
            row.update(status=body.status, response=body.response.strip(), version=row["version"] + 1,
                       responded_by=actor["id"], updated=time.time())
            put(db, key, row)
            db.insert("audit", {"actor": actor["id"], "action": "tool_request_responded", "resource": request_id,
                               "detail": json.dumps({"status": row["status"], "response": row["response"],
                                                    "version": row["version"]}), "created": time.time()})
            return row
        return transaction(save)
    return routes
