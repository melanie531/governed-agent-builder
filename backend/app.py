import asyncio
from contextlib import asynccontextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import time
import uuid
import zipfile
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .catalog import PERSONAS, SAMPLE_DATASET
from .harness import evaluate, run_case
from .schemas import CapabilityRequest, CatalogUpdate, Decision, DefinitionInput, Deploy, Grant, Invoke, Login, PolicyUpdate
from .store import Store
from .hosted_auth import HostedAuth

ROOT = Path(__file__).resolve().parent.parent
TERMINAL = {"PASS", "NEEDS_CHANGES"}
ACTIVE = ("VALIDATING", "PREPARING", "LOCAL_RUNTIME_READY", "TESTING", "EVALUATING")


def uid():
    return uuid.uuid4().hex


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def audit(db, actor, action, resource, detail=""):
    db.insert('audit', {'actor': actor, 'action': action, 'resource': resource, 'detail': detail, 'created': time.time()})


def event(db, job_id, stage, detail):
    db.insert('events', {'job': job_id, 'stage': stage, 'detail': json.dumps(detail), 'created': time.time()})


def policy(db):
    return json.loads(db.select('settings', columns=['body'], where=[('key', '=', 'policy')]).fetchone()[0])


def resource(db, table, resource_id):
    if table not in ("components", "foundations"):
        raise ValueError("Invalid catalog authority")
    row = db.select(table, columns=['body'], where=[('id', '=', resource_id)]).fetchone()
    if not row:
        raise HTTPException(422, "Unknown catalog resource")
    return json.loads(row[0])


def allowed(db, persona, component, foundation):
    kind = component["kind"] + "s"
    return (component["approved"] and component["id"] in foundation[kind]
            and (not component["external"] or persona["external_allowed"])
            and db.select('grants', where=[('persona', '=', persona['id']), ('component', '=', component['id'])]).fetchone() is not None)


def validate_definition(db, persona, definition):
    foundation = resource(db, "foundations", definition["foundation_id"])
    if not foundation["approved"] or foundation["version"] != definition["foundation_version"]:
        raise HTTPException(403, "Foundation was revoked or version changed; revise and retest")
    selected = [definition["model_id"], *definition["tools"], *definition["skills"]]
    if len(set(selected)) != len(selected) or set(selected) != set(definition["component_versions"]):
        raise HTTPException(422, "Selected components and pinned version keys must match exactly")
    for component_id in selected:
        component = resource(db, "components", component_id)
        expected_kind = "model" if component_id == definition["model_id"] else "tool" if component_id in definition["tools"] else "skill"
        if component["kind"] != expected_kind or not allowed(db, persona, component, foundation):
            raise HTTPException(403, f"Component not authorized or compatible: {component_id}")
        if component["version"] != definition["component_versions"][component_id]:
            raise HTTPException(409, "Component version changed; revise and retest")
    return foundation


def agent_access(db, persona, agent_id):
    row = db.select('agents', where=[('id', '=', agent_id)]).fetchone()
    # Admin governance is not a blanket right to read another user's prompt/dataset.
    if not row or row["owner"] != persona["id"] or row["workspace"] != persona["workspace"]:
        raise HTTPException(404, "Agent not found")
    return dict(row)


def get_version(db, agent_id, version):
    row = db.select('versions', where=[('agent', '=', agent_id), ('version', '=', version)]).fetchone()
    if not row:
        raise HTTPException(404, "Version not found")
    return json.loads(row["body"])


def create_app(db_path=None, demo_mode=None, worker_enabled=True, public_url=None, repository=None):
    hosted = os.getenv("HOSTED_PREVIEW") == "1"
    demo = os.getenv("DEMO_MODE") == "1" if demo_mode is None else demo_mode
    if hosted and demo:
        raise RuntimeError("HOSTED_PREVIEW and DEMO_MODE are mutually exclusive")
    mode = os.getenv("EXECUTION_MODE", "local")
    if mode != "local":
        raise RuntimeError("AWS mode disabled: configure and validate Okta, scoped Runtime deployment, both Gateways, Registry and evaluation adapters first. No simulation fallback.")
    if not demo and not hosted:
        raise RuntimeError("Demo authentication is disabled. Set DEMO_MODE=1 only for loopback synthetic demos. Production Okta enforcement is not implemented; startup fails closed.")
    if not hosted and os.getenv("HOST", "127.0.0.1") not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError("Demo must bind to loopback only")
    port = os.getenv("PORT", "5187")
    public = public_url or os.getenv("PUBLIC_URL", f"http://127.0.0.1:{port}")
    parsed = urlparse(public)
    if hosted:
        if parsed.scheme != "https" or not parsed.hostname or parsed.hostname in ("localhost", "127.0.0.1", "::1") or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/") or parsed.port not in (None, 443):
            raise RuntimeError("HOSTED_PREVIEW requires an exact public HTTPS origin")
        if not os.getenv("STATE_PATH") and not db_path and repository is None:
            raise RuntimeError("HOSTED_PREVIEW requires persistent STATE_PATH")
        origins = {public.rstrip("/")}
    else:
        if parsed.hostname not in ("localhost", "127.0.0.1", "::1") or parsed.scheme not in ("http", "https") or parsed.username or parsed.query or parsed.path not in ("", "/"):
            raise RuntimeError("PUBLIC_URL must be a loopback origin without a path or credentials")
        origins = {public.rstrip("/"), f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    hosts = {urlparse(x).netloc for x in origins}
    store = repository if repository is not None else Store(db_path or (os.environ["STATE_PATH"] if hosted else str(ROOT / "artifacts/state.sqlite")), seed_personas=not hosted)
    auth = HostedAuth(store, public) if hosted else None
    mode_label = "CLOUD-HOSTED DEMO" if hosted else "LOCAL SIMULATION"

    def principal(db, subject):
        if not hosted:
            if subject not in PERSONAS:
                raise HTTPException(404, "Identity not found")
            return PERSONAS[subject]
        row = db.select('principals', columns=['body'], where=[('id', '=', subject), ('expires', '>', time.time())]).fetchone()
        if not row:
            raise HTTPException(403, "Studio membership expired; sign in and retry")
        return json.loads(row[0])

    def principal_list(db):
        if not hosted:
            return list(PERSONAS.values())
        return [json.loads(r[0]) for r in db.select('principals', columns=['body'], where=[('expires', '>', time.time())])]

    @asynccontextmanager
    async def lifespan(app):
        app.state.wake = asyncio.Event()
        if worker_enabled:
            with store.tx() as db:
                # A single supported worker owns this local database. Active jobs restart
                # safely because fixture execution has no external side effects.
                for job in db.select('jobs', columns=['id'], where=[('stage', "not_in", ['PASS','NEEDS_CHANGES'])]).fetchall():
                    db.update('jobs', {'stage': 'VALIDATING', 'updated': time.time()}, where=[('id', '=', job['id'])])
                    event(db, job["id"], "RECOVERED", {"message": "Resumed durable local job after process restart"})
            task = asyncio.create_task(worker_loop())
        yield
        if worker_enabled:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="Agent Studio · " + mode_label, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.wake = asyncio.Event()
    app.state.hosted_auth = auth

    @app.middleware("http")
    async def security(request: Request, call_next):
        # No forwarded identity/role/header trust. Host checks prevent DNS rebinding.
        if request.headers.get("host") not in hosts:
            return JSONResponse({"detail": "Unapproved host"}, status_code=400)
        callback_request = hosted and request.url.path == "/auth/callback" and request.method == "GET"
        if request.headers.get("sec-fetch-site") == "cross-site" and not callback_request:
            return JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)
        api_request = request.url.path == "/api" or request.url.path.startswith("/api/")
        if hosted and api_request:
            try:
                request.state.persona, request.state.csrf = await asyncio.to_thread(auth.authenticate, request)
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers={"Cache-Control": "no-store"})
            if request.url.path.startswith("/api/demo/"):
                return JSONResponse({"detail": "Not found"}, status_code=404, headers={"Cache-Control": "no-store"})
            if request.method not in ("GET", "HEAD", "OPTIONS") and not secrets.compare_digest(request.headers.get("x-csrf-token", ""), request.state.csrf):
                return JSONResponse({"detail": "CSRF token required"}, status_code=403)
        # Enforce while reading, rather than allocating an unbounded upload first.
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 65536:
                return JSONResponse({"detail": "Request exceeds 64 KiB limit"}, status_code=413)
            chunks.append(chunk)
        request._body = b"".join(chunks)
        mutating = request.method not in ("GET", "HEAD", "OPTIONS")
        if mutating and request.headers.get("origin") not in origins:
            return JSONResponse({"detail": "Same-origin request required"}, status_code=403)
        if not hosted and request.url.path.startswith("/api/") and request.url.path not in ("/api/demo/personas", "/api/demo/session"):
            with store.tx() as db:
                row = db.select('sessions', where=[('id', '=', request.cookies.get('gab_session', '')), ('expires', '>', time.time())]).fetchone()
            if not row:
                return JSONResponse({"detail": "Choose a DEV ONLY demo persona to continue"}, status_code=401)
            request.state.persona = PERSONAS[row["persona"]]
            request.state.csrf = row["csrf"]
            if mutating and not secrets.compare_digest(request.headers.get("x-csrf-token", ""), row["csrf"]):
                return JSONResponse({"detail": "CSRF token required"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self' http://127.0.0.1:* http://localhost:*; base-uri 'self'; form-action 'self'"
        if hosted:
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    if hosted:
        @app.get("/auth/login")
        def hosted_login():
            return auth.start()

        @app.get("/auth/callback")
        async def hosted_callback(request: Request):
            return await auth.callback(request)

        @app.post("/api/auth/logout")
        def hosted_logout(request: Request):
            return auth.logout(request)

    @app.get("/studio-config.json")
    def studio_config():
        return {"hosted": hosted, "mode": mode_label}

    def who(request, admin=False):
        persona = request.state.persona
        if admin and persona["role"] != "admin":
            raise HTTPException(403, "Platform admin required")
        return persona

    @app.get("/api/demo/personas")
    def personas():
        if hosted:
            raise HTTPException(404, "Not found")
        return {"mode": mode_label, "notice": "DEV ONLY identity selector. Not production authentication. Synthetic data only.", "personas": list(PERSONAS.values())}

    @app.post("/api/demo/session")
    def login(data: Login, request: Request, response: Response):
        if hosted:
            raise HTTPException(404, "Not found")
        session_id, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with store.tx() as db:
            db.delete('sessions', where=[('expires', '<', time.time()), ('id', '=', request.cookies.get('gab_session', ''))], any_of=True)
            db.insert('sessions', {'id': session_id, 'persona': data.persona_id, 'csrf': csrf, 'expires': time.time() + 8 * 3600})
            audit(db, data.persona_id, "demo_session", "local", "DEV ONLY persona selected")
        response.set_cookie("gab_session", session_id, httponly=True, samesite="strict", secure=parsed.scheme == "https", max_age=8 * 3600)
        return {"persona": PERSONAS[data.persona_id], "csrf": csrf, "mode": mode_label}

    @app.get("/api/me")
    def me(request: Request):
        return {"persona": who(request), "csrf": request.state.csrf, "mode": mode_label}

    @app.get("/api/build-options")
    def options(request: Request, foundation_id: str | None = None, model_id: str | None = None):
        persona = who(request)
        with store.tx() as db:
            foundations = [json.loads(r[0]) for r in db.select('foundations', columns=['body'])]
            foundations = [f for f in foundations if f["approved"]]
            choices = {"models": [], "tools": [], "skills": []}
            if foundation_id:
                foundation = resource(db, "foundations", foundation_id)
                if not foundation["approved"]:
                    raise HTTPException(403, "Foundation unavailable")
                if model_id and not allowed(db, persona, resource(db, "components", model_id), foundation):
                    raise HTTPException(403, "Model unavailable")
                for row in db.select('components', columns=['body']):
                    component = json.loads(row[0])
                    if allowed(db, persona, component, foundation):
                        choices[component["kind"] + "s"].append(component)
            return {"foundations": foundations, "choices": choices, "sample_dataset": SAMPLE_DATASET, "policy": policy(db), "catalog_revision": digest([foundations, choices]), "recalculate_on": ["foundation_id", "model_id"]}

    def save_definition(db, persona, data, agent_id=None):
        if persona["role"] != "business":
            raise HTTPException(403, "A business workspace membership is required to create agents")
        payload = data.model_dump(exclude={"base_version"})
        foundation = validate_definition(db, persona, payload)
        if agent_id:
            agent = agent_access(db, persona, agent_id)
            if data.base_version != agent["current_version"]:
                raise HTTPException(409, "Stale draft; reload the current version")
            version = agent["current_version"] + 1
            db.update('agents', {'current_version': version}, where=[('id', '=', agent_id)])
        else:
            if data.base_version is not None:
                raise HTTPException(422, "New agents cannot have base_version")
            count = db.select('agents', count=True, where=[('owner', '=', persona['id'])]).fetchone()[0]
            if count >= 100:
                raise HTTPException(429, "Local demo cap: 100 agents per identity")
            agent_id, version = uid(), 1
            db.insert('agents', {'id': agent_id, 'owner': persona['id'], 'workspace': persona['workspace'], 'current_version': version, 'created': time.time()})
        payload.update({"agent_id": agent_id, "version": version, "owner": persona["id"], "workspace": persona["workspace"], "foundation_manifest": foundation, "foundation_manifest_digest": digest(foundation), "schema_version": "1", "mode": mode_label, "prompt_ref": "sha256:" + digest(payload["prompt"]), "dataset_ref": "sha256:" + digest(payload["dataset"]), "rubric_ref": "sha256:" + digest(payload["rubric"]), "decision_policy_version": policy(db)["version"]})
        payload["digest"] = digest(payload)
        db.insert('versions', {'agent': agent_id, 'version': version, 'digest': payload['digest'], 'body': json.dumps(payload), 'created': time.time()})
        audit(db, persona["id"], "definition_created", agent_id, f"version={version}, digest={payload['digest']}")
        return payload

    @app.post("/api/agents", status_code=201)
    def create(data: DefinitionInput, request: Request):
        with store.tx() as db:
            return save_definition(db, who(request), data)

    @app.post("/api/agents/{agent_id}/versions", status_code=201)
    def revise(agent_id: str, data: DefinitionInput, request: Request):
        with store.tx() as db:
            return save_definition(db, who(request), data, agent_id)

    @app.get("/api/agents")
    def agents(request: Request):
        persona = who(request)
        with store.tx() as db:
            rows = db.select('agents', where=[('owner', '=', persona['id']), ('workspace', '=', persona['workspace'])], order='created', descending=True).fetchall()
            result = []
            for row in rows:
                definition = get_version(db, row["id"], row["current_version"])
                latest = db.select('jobs', columns=['id', 'stage', 'version'], where=[('agent', '=', row['id'])], order='created', descending=True, limit=1).fetchone()
                result.append({**dict(row), "name": definition["name"], "foundation_id": definition["foundation_id"], "job": dict(latest) if latest else None})
            return result

    @app.get("/api/agents/{agent_id}")
    def get_agent(agent_id: str, request: Request):
        with store.tx() as db:
            agent = agent_access(db, who(request), agent_id)
            versions = [dict(r) for r in db.select('versions', columns=['version', 'digest', 'created'], where=[('agent', '=', agent_id)], order='version', descending=True)]
            jobs = [dict(r) for r in db.select('jobs', columns=['id', 'version', 'stage', 'created'], where=[('agent', '=', agent_id)], order='created', descending=True)]
            return {**agent, "definition": get_version(db, agent_id, agent["current_version"]), "versions": versions, "jobs": jobs}

    @app.post("/api/agents/{agent_id}/deploy-test", status_code=202)
    async def deploy(agent_id: str, data: Deploy, request: Request):
        persona = who(request)
        with store.tx() as db:
            agent = agent_access(db, persona, agent_id)
            if agent["current_version"] != data.version:
                raise HTTPException(409, "Only the current definition can be tested")
            definition = get_version(db, agent_id, data.version)
            validate_definition(db, persona, definition)
            existing = db.select('jobs', where=[('agent', '=', agent_id), ('requester', '=', persona['id']), ('idem', '=', data.idempotency_key)]).fetchone()
            if existing:
                if existing["version"] != data.version:
                    raise HTTPException(409, "Idempotency key already binds a different version")
                return {"job_id": existing["id"], "stage": existing["stage"], "reused": True}
            if db.select('jobs', where=[('agent', '=', agent_id), ('stage', "not_in", ['PASS','NEEDS_CHANGES'])]).fetchone():
                raise HTTPException(409, "A job already owns this agent; wait for completion")
            if db.select('jobs', count=True, where=[('stage', "not_in", ['PASS','NEEDS_CHANGES'])]).fetchone()[0] >= 8:
                raise HTTPException(429, "Local queue cap reached (8)")
            if db.select('jobs', count=True, where=[('requester', '=', persona['id']), ('created', '>', time.time() - 3600)]).fetchone()[0] >= 30:
                raise HTTPException(429, "Local budget cap: 30 jobs per identity per hour")
            job_id, now = uid(), time.time()
            db.insert('jobs', {'id': job_id, 'agent': agent_id, 'version': data.version, 'requester': persona['id'], 'idem': data.idempotency_key, 'stage': 'VALIDATING', 'created': now, 'updated': now, 'deadline': now + (900 if repository is not None else 60)})
            event(db, job_id, "VALIDATING", {"message": "Local deploy/test accepted", "digest": definition["digest"], "mode": mode_label})
            audit(db, persona["id"], "deploy_test", agent_id, job_id)
            if repository is not None and hosted:
                from .hosted_auth import SESSION_COOKIE, sha
                db.insert("job_authority", {"id": job_id, "session_hash": sha(request.cookies.get(SESSION_COOKIE, ""))})
        request.app.state.wake.set()
        return {"job_id": job_id, "stage": "VALIDATING", "reused": False}

    @app.get("/api/jobs/{job_id}")
    def job_result(job_id: str, request: Request):
        with store.tx() as db:
            row = db.select('jobs', where=[('id', '=', job_id)]).fetchone()
            if not row:
                raise HTTPException(404, "Job not found")
            agent = agent_access(db, who(request), row["agent"])
            events = [{**dict(r), "detail": json.loads(r["detail"])} for r in db.select('events', where=[('job', '=', job_id)], order='id')]
            result = json.loads(row["result"]) if row["result"] else None
            return {**dict(row), "result": result, "events": events, "current_version": agent["current_version"], "stale": row["version"] != agent["current_version"], "mode": mode_label}

    @app.post("/api/agents/{agent_id}/invoke")
    def invoke(agent_id: str, data: Invoke, request: Request):
        persona = who(request)
        with store.tx() as db:
            agent = agent_access(db, persona, agent_id)
            if data.version != agent["current_version"]:
                raise HTTPException(409, "Current version required; revise and retest")
            definition = get_version(db, agent_id, data.version)
            validate_definition(db, persona, definition)
            job = db.select('jobs', where=[('agent', '=', agent_id), ('version', '=', data.version)], order='created', descending=True, limit=1).fetchone()
            if not job or job["stage"] != "PASS":
                raise HTTPException(409, "Current version has not passed local checks")
            result = json.loads(job["result"])
            if result["definition_digest"] != definition["digest"] or result["policy_version"] != policy(db)["version"]:
                raise HTTPException(409, "Stale evaluation evidence or changed policy; retest required")
            if db.select('audit', count=True, where=[('actor', '=', persona['id']), ('action', '=', 'local_invoke'), ('created', '>', time.time() - 60)]).fetchone()[0] >= 30:
                raise HTTPException(429, "Local invocation cap: 30 per minute")
            output = run_case(definition, data.input)
            audit(db, persona["id"], "local_invoke", agent_id, f"version={data.version}")
            return {**output, "mode": mode_label, "version": data.version}

    @app.get("/api/agents/{agent_id}/export")
    def export(agent_id: str, request: Request):
        with store.tx() as db:
            agent = agent_access(db, who(request), agent_id)
            definition = get_version(db, agent_id, agent["current_version"])
            export_policy = policy(db)
            audit(db, who(request)["id"], "export", agent_id, f"version={agent['current_version']}")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("definition.json", json.dumps(definition, indent=2))
            archive.writestr("prompt.txt", definition["prompt"])
            archive.writestr("dataset.json", json.dumps(definition["dataset"], indent=2))
            archive.writestr("rubric.json", json.dumps(definition["rubric"], indent=2))
            archive.writestr("foundation-manifest.json", json.dumps(definition["foundation_manifest"], indent=2))
            archive.writestr("config.json", json.dumps({"mode": "LOCAL SIMULATION", "exported_from": mode_label, "component_versions": definition["component_versions"], "evaluation_policy": export_policy}, indent=2))
            archive.writestr("harness.py", (ROOT / "backend/harness.py").read_text())
            archive.writestr("run.py", 'import json\nfrom pathlib import Path\nfrom harness import evaluate\nd=json.loads(Path("definition.json").read_text())\np=json.loads(Path("config.json").read_text())["evaluation_policy"]\nprint(json.dumps(evaluate(d,p),indent=2))\n')
            archive.writestr("requirements.txt", "# Python >=3.12; portable runner uses standard library only. No dependencies.\n")
            archive.writestr("uv.lock", (ROOT / "uv.lock").read_text())
            archive.writestr("README.md", "# Local fixture export\n\nRun `python3 run.py` in this directory. Python >=3.12, standard library only.\n\nLOCAL SIMULATION. No model calls, credentials, cloud logs, or production readiness. The application dependency lock is included for provenance; the portable harness needs none. Definition is immutable; digest covers every field except digest itself. config.json pins the export-time gate policy. Natural language rubric is preserved; no LLM judge runs. Prompt logic and synthetic sources are in harness.py. Editing files creates an ungoverned local copy, not a new platform version.\n")
        if repository is not None and hosted:
            import boto3
            key = f"exports/{who(request)['id']}/{agent_id}/{agent['current_version']}/{uid()}.zip"
            boto3.client("s3").put_object(Bucket=os.environ["EXPORT_BUCKET"], Key=key, Body=buffer.getvalue(), ContentType="application/zip", ServerSideEncryption="AES256")
        return Response(buffer.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="agent-{agent_id}-v{agent["current_version"]}.zip"'})

    @app.get("/api/capabilities")
    def capabilities(request: Request):
        persona = who(request)
        with store.tx() as db:
            grants = {r[0] for r in db.select('grants', columns=['component'], where=[('persona', '=', persona['id'])])}
            return [{**json.loads(r[0]), "granted": json.loads(r[0])["id"] in grants, "data_policy_allowed": not json.loads(r[0])["external"] or persona["external_allowed"]} for r in db.select('components', columns=['body'])]

    @app.get("/api/requests")
    def requests(request: Request):
        persona = who(request)
        with store.tx() as db:
            if persona["role"] == "admin":
                return [dict(r) for r in db.select('requests', order='created', descending=True)]
            return [dict(r) for r in db.select('requests', where=[('requester', '=', persona['id']), ('workspace', '=', persona['workspace'])], order='created', descending=True)]

    @app.post("/api/requests", status_code=201)
    def submit_request(data: CapabilityRequest, request: Request):
        persona = who(request)
        if persona["role"] != "business":
            raise HTTPException(403, "Business identity required")
        with store.tx() as db:
            resource(db, "components", data.component_id)
            if db.select('requests', where=[('requester', '=', persona['id']), ('component', '=', data.component_id), ('status', '=', 'PENDING')]).fetchone():
                raise HTTPException(409, "A request is already pending")
            request_id = uid()
            db.insert('requests', {'id': request_id, 'requester': persona['id'], 'workspace': persona['workspace'], 'component': data.component_id, 'reason': data.reason, 'status': 'PENDING', 'decision': None, 'created': time.time()})
            audit(db, persona["id"], "capability_requested", request_id, data.component_id)
        return {"id": request_id}

    @app.get("/api/admin/catalog")
    def admin_catalog(request: Request):
        who(request, True)
        with store.tx() as db:
            return {"foundations": [json.loads(r[0]) for r in db.select('foundations', columns=['body'])], "components": [json.loads(r[0]) for r in db.select('components', columns=['body'])], "grants": [dict(r) for r in db.select('grants')], "personas": principal_list(db), "policy": policy(db), "history": [dict(r) for r in db.select('catalog_history', order='id', descending=True, limit=100)]}

    @app.post("/api/admin/grants")
    def grant(data: Grant, request: Request):
        persona = who(request, True)
        with store.tx() as db:
            component = resource(db, "components", data.component_id)
            if principal(db, data.persona_id)["role"] != "business":
                raise HTTPException(403, "Business identity required")
            if data.enabled and (not component["approved"] or component["external"] and not principal(db, data.persona_id)["external_allowed"]):
                raise HTTPException(403, "Approval or workspace data policy blocks this grant")
            if data.enabled:
                db.insert('grants', {'persona': data.persona_id, 'component': data.component_id}, ignore=True)
            else:
                db.delete('grants', where=[('persona', '=', data.persona_id), ('component', '=', data.component_id)])
            audit(db, persona["id"], "grant" if data.enabled else "revoke", data.persona_id, data.component_id)
        return {"ok": True}

    @app.post("/api/admin/requests/{request_id}/decision")
    def decide(request_id: str, data: Decision, request: Request):
        persona = who(request, True)
        with store.tx() as db:
            row = db.select('requests', where=[('id', '=', request_id)]).fetchone()
            if not row:
                raise HTTPException(404, "Request not found")
            if row["requester"] == persona["id"]:
                raise HTTPException(403, "Self-approval prohibited")
            if row["status"] != "PENDING":
                raise HTTPException(409, "Request already decided")
            component = resource(db, "components", row["component"])
            if data.approve:
                if not component["approved"] or component["external"] and not principal(db, row["requester"])["external_allowed"]:
                    raise HTTPException(403, "Approval or data policy blocks this capability")
                db.insert('grants', {'persona': row['requester'], 'component': row['component']}, ignore=True)
            db.update('requests', {'status': 'APPROVED' if data.approve else 'REJECTED', 'decision': data.reason}, where=[('id', '=', request_id)])
            audit(db, persona["id"], "request_decided", request_id, "approved" if data.approve else "rejected")
        return {"ok": True, "notice": "Existing seeded capability grant updated; no connector was created"}

    @app.post("/api/admin/catalog/{authority}/{resource_id}")
    def update_catalog(authority: str, resource_id: str, data: CatalogUpdate, request: Request):
        persona = who(request, True)
        if authority not in ("components", "foundations"):
            raise HTTPException(404, "Unknown catalog authority")
        with store.tx() as db:
            item = resource(db, authority, resource_id)
            db.insert('catalog_history', {'resource': resource_id, 'body': json.dumps(item), 'created': time.time()})
            item["approved"] = data.approved
            item["version"] = str(int(item["version"]) + 1) if authority == "components" else f"1.0.{int(item['version'].split('.')[-1]) + 1}"
            db.update(authority, {'body': json.dumps(item)}, where=[('id', '=', resource_id)])
            audit(db, persona["id"], "catalog_revision", resource_id, item["version"])
        return item

    @app.post("/api/admin/policy")
    def update_policy(data: PolicyUpdate, request: Request):
        persona = who(request, True)
        with store.tx() as db:
            previous = policy(db)
            current = {**data.model_dump(), "version": previous["version"] + 1}
            db.insert('catalog_history', {'resource': 'policy', 'body': json.dumps(previous), 'created': time.time()})
            db.update('settings', {'body': json.dumps(current)}, where=[('key', '=', 'policy')])
            audit(db, persona["id"], "policy_revision", "mandatory-gates", str(current["version"]))
        return current

    @app.get("/api/admin/audit")
    def audit_log(request: Request):
        who(request, True)
        with store.tx() as db:
            return [dict(r) for r in db.select('audit', order='id', descending=True, limit=300)]

    def step_job(job_id):
        with store.tx() as db:
            job = dict(db.select('jobs', where=[('id', '=', job_id)]).fetchone())
            if job["stage"] in TERMINAL:
                return
            try:
                if time.time() > job["deadline"]:
                    raise HTTPException(408, "Job deadline exceeded; submit a new test")
                persona = principal(db, job["requester"])
                if repository is not None and hosted:
                    authority = db.select("job_authority", where=[("id", "=", job_id)]).fetchone()
                    session = db.select("hosted_sessions", where=[("id_hash", "=", authority["session_hash"]), ("expires", ">", time.time())]).fetchone() if authority else None
                    if not session or session["subject"] != job["requester"]:
                        raise HTTPException(403, "Job authorization expired or revoked; sign in and retest")
                    claims = auth.verify(session["access_token"], "access")
                    if claims["sub"] != job["requester"]:
                        raise HTTPException(403, "Job subject binding invalid")
                agent = agent_access(db, persona, job["agent"])
                if agent["current_version"] != job["version"]:
                    raise HTTPException(409, "Definition changed during test; retest current version")
                definition = get_version(db, job["agent"], job["version"])
                validate_definition(db, persona, definition)
                stage = job["stage"]
                if stage == "EVALUATING":
                    result = evaluate(definition, policy(db))
                    result.update({"definition_digest": definition["digest"], "dataset_ref": definition["dataset_ref"], "version": job["version"], "policy_version": policy(db)["version"]})
                    next_stage = "PASS" if result["passed"] else "NEEDS_CHANGES"
                    db.update('jobs', {'result': json.dumps(result)}, where=[('id', '=', job_id)])
                    for case in result["cases"]:
                        event(db, job_id, "CASE_EVIDENCE", {"case_id": case["id"], "trace": case["trace"], "score": case["score"]})
                    detail = {"message": result["gate"], "score": result["score"], "judge": result["judge"]}
                else:
                    next_stage = ACTIVE[ACTIVE.index(stage) + 1]
                    detail = {"message": {"PREPARING": "Pinned fixture harness loaded", "LOCAL_RUNTIME_READY": "Local fixture runner ready (not AWS Runtime)", "TESTING": "Bounded synthetic cases scheduled", "EVALUATING": "Executing fixture cases and deterministic checks"}[next_stage]}
                db.update('jobs', {'stage': next_stage, 'updated': time.time()}, where=[('id', '=', job_id)])
                event(db, job_id, next_stage, detail)
            except HTTPException as exc:
                result = {"passed": False, "gate": "Needs changes", "error": exc.detail, "mode": mode_label, "production_ready": False}
                db.update('jobs', {'stage': 'NEEDS_CHANGES', 'result': json.dumps(result), 'updated': time.time()}, where=[('id', '=', job_id)])
                event(db, job_id, "NEEDS_CHANGES", {"message": exc.detail})

    async def execute_job(job_id):
        try:
            for _ in ACTIVE:
                await asyncio.to_thread(step_job, job_id)
                await asyncio.sleep(0)  # cooperative yield, no fabricated stage delay
        except asyncio.CancelledError:
            raise
        except Exception:
            with store.tx() as db:
                db.update('jobs', {'stage': 'NEEDS_CHANGES', 'result': json.dumps({'passed': False, 'gate': 'Needs changes', 'error': 'Local worker error; inspect server diagnostics', 'mode': mode_label}), 'updated': time.time()}, where=[('id', '=', job_id)])
                event(db, job_id, "NEEDS_CHANGES", {"message": "Sanitized worker error"})

    async def worker_loop():
        # Event-driven wake-up, max two independent agents executing concurrently.
        running = set()
        while True:
            with store.tx() as db:
                jobs = [r["id"] for r in db.select('jobs', columns=['id'], where=[('stage', "not_in", ['PASS','NEEDS_CHANGES'])], order='created') if r["id"] not in running][:2 - len(running)]
                for job_id in jobs:
                    db.update('jobs', {}, where=[('id', '=', job_id)], increments={'attempts': 1})
            if jobs:
                running.update(jobs)
                try:
                    await asyncio.gather(*(execute_job(j) for j in jobs))
                finally:
                    running.difference_update(jobs)
                continue
            app.state.wake.clear()
            # Close enqueue/clear race before sleeping.
            with store.tx() as db:
                pending = db.select('jobs', where=[('stage', "not_in", ['PASS','NEEDS_CHANGES'])], limit=1).fetchone()
            if pending:
                continue
            await app.state.wake.wait()

    app.state.step_job = step_job
    dist = ROOT / "frontend/dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "API route not found")
        index = dist / "index.html"
        if not index.exists():
            return JSONResponse({"detail": "Frontend not built. Run ./scripts/start.sh"}, status_code=503)
        return FileResponse(index)

    return app
