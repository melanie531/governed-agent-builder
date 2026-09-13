import asyncio
from contextlib import asynccontextmanager
import hashlib
import io
import json
import os
import re
from pathlib import Path
import secrets
import time
import uuid
import zipfile
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .foundation_approval import RegisterFoundation, ApproveFoundation, register as register_source, compile_approval, platform_metadata, FinalizeFoundation, finalize_artifact
from . import self_service_admission as self_service
from .foundation_runs import get as get_foundation_record
from .catalog import PERSONAS, SAMPLE_DATASET
from foundations.web_research import FOUNDATION as WEB_FOUNDATION, SKILLS as REPORT_SKILLS, skill_binding
from .harness import evaluate, run_case
from .schemas import CapabilityRequest, CatalogUpdate, Decision, DefinitionInput, Deploy, GeneralRequest, GeneralRequestStatus, Grant, Invoke, Login, PolicyUpdate
from .store import Store
from .hosted_auth import HostedAuth
from .live_catalog import projection, visibility, has_grant, grant_scope, configured_catalog
from . import builder_catalog

ROOT = Path(__file__).resolve().parent.parent
TERMINAL = {"PASS", "NEEDS_CHANGES", "LIVE_PASS", "BLOCKED"}
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
    return (visibility(component, persona) and component.get("integration_ready", True) and component["id"] in foundation.get(kind, [])
            and (not component["external"] or persona["external_allowed"])
            and has_grant(db, persona, component))


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


def create_app(db_path=None, demo_mode=None, worker_enabled=True, public_url=None, repository=None, catalog_provider=None, foundation_jobs=None):
    catalog_mode = os.getenv("CATALOG_MODE", "fixture")
    if catalog_mode not in ("fixture", "live"):
        raise RuntimeError("CATALOG_MODE must be fixture or live")
    # Construct native discovery only after explicit server-owned source approval.
    catalog_configuration_invalid = False
    if catalog_mode == 'live' and catalog_provider is None:
        try:
            catalog_provider = configured_catalog()
        except Exception:
            catalog_configuration_invalid = True
    def catalog_records(db):
        if catalog_mode == "live":
            if catalog_provider is None:
                raise HTTPException(503, "NotConnected: Live catalog configuration invalid; no fixture fallback" if catalog_configuration_invalid else "NotConnected: Live catalog integration is not configured; no fixture fallback")
            try:
                return catalog_provider.records()
            except Exception:
                raise HTTPException(503, 'Live catalog unavailable; no fixture fallback') from None
        return [json.loads(r[0]) for r in db.select('components', columns=['body'])]

    def catalog_resource(db, persona, component_id):
        records = catalog_records(db)
        item = next((c for c in records if c['id'] == component_id), None)
        if item and item.get('parent_id'):
            parent = next((c for c in records if c['id'] == item['parent_id']), None)
            if not parent or not visibility(parent, persona):
                raise HTTPException(404, "Capability not found")
        if not item or not visibility(item, persona):
            raise HTTPException(404, "Capability not found")
        return item

    def validate_current(db, persona, definition, *, live_execution=False):
        if definition.get("catalog_mode") == "live" and catalog_mode != "live":
            raise HTTPException(503, "Live draft cannot execute in fixture mode")
        if definition.get("foundation_id") == "web-research" or definition.get("research") is not None:
            raise HTTPException(503, "NOT_CONFIGURED: Web research Gateway deployment and live authorization are not connected; no fixture fallback")
        if catalog_mode == "live":
            foundation = resource(db, "foundations", definition["foundation_id"])
            readiness = live_readiness(db, persona, definition, foundation, definition)
            if not live_execution or not readiness["deployable"]:
                raise HTTPException(503, {"code": "LIVE_EXECUTION_BLOCKED", **readiness})
            return foundation
        return validate_definition(db, persona, definition)

    def live_readiness(db, persona, definition, foundation, previous=None):
        try:
            records = catalog_records(db)
            unavailable = None
        except HTTPException as exc:
            records, unavailable = [], str(exc.detail)
        deployment_issues = (foundation_jobs.readiness_issues(db, definition, persona)
                             if foundation_jobs is not None else [
                                 {'code': 'deployment_driver_missing', 'message': 'Configure the server-owned Foundation deployment driver; no fixture fallback'}])
        if worker_enabled:
            deployment_issues.append({'code': 'live_worker_required', 'message': 'Live deployment requires the configured durable worker, not the fixture worker'})
        result = builder_catalog.assess(db, persona, definition, foundation, records, previous, deployment_issues)
        if unavailable:
            result['issues'].append({'code': 'NotConnected', 'message': unavailable})
            result['deployable'] = False
        return result

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
                for job in db.select('jobs', columns=['id'], where=[('stage', "not_in", ['PASS','NEEDS_CHANGES','LIVE_PASS','BLOCKED'])]).fetchall():
                    from .foundation_runs import get as get_run
                    if get_run(db, 'foundation-run:' + job['id']) or get_run(db, 'foundation-pending:' + job['id']):
                        continue
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
        verification_request = hosted and request.url.path.startswith("/auth/verification/")
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
        if verification_request:
            try:
                request.state.verification = await asyncio.to_thread(auth.pending, request)
                if request.method == "GET":
                    if request.headers.get("sec-fetch-site") != "same-origin" or request.headers.get("x-studio-verification") != "1":
                        raise HTTPException(403, "Same-origin browser request required")
                elif (request.headers.get("origin") not in origins
                      or request.headers.get("sec-fetch-site") not in (None, "same-origin")
                      or not secrets.compare_digest(request.headers.get("x-csrf-token", ""), request.state.verification["csrf"])):
                    raise HTTPException(403, "Same-origin request and CSRF token required")
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers={"Cache-Control": "no-store"})
        if request.headers.get("sec-fetch-site") == "cross-site" and not callback_request and not verification_request:
            return JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)
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
        if callback_request and response.status_code >= 400:
            from fastapi.responses import HTMLResponse
            from .hosted_auth import FLOW_COOKIE, PENDING_COOKIE, SESSION_COOKIE
            # Never reflect provider error descriptions or callback parameters.
            # An old/mismatched callback must not destroy a newer sign-in flow.
            response = HTMLResponse('<!doctype html><html lang="en"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                '<title>Sign-in needs restarting · Agent Studio</title><body><main>'
                '<h1>Sign-in could not be completed</h1><p>This sign-in link may have expired '
                'or been replaced by a newer attempt. This does not mean your password is wrong.</p>'
                '<p>Return to Studio and start sign-in in one tab. Do not refresh this callback page.</p>'
                '<p><a href="/">Return to Agent Studio</a></p></main></body></html>',
                status_code=response.status_code)
            cookies = [PENDING_COOKIE, SESSION_COOKIE]
            if getattr(request.state, "matched_signin_flow", False):
                cookies.append(FLOW_COOKIE)
            for cookie in cookies:
                response.delete_cookie(cookie, secure=True, httponly=True, samesite="lax" if cookie == FLOW_COOKIE else "strict")
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
        def hosted_login(request: Request):
            return auth.start(request)

        @app.get("/auth/callback")
        async def hosted_callback(request: Request):
            return await auth.callback(request)

        @app.get("/auth/verification/status")
        def verification_status(request: Request):
            return auth.verification_status(request.state.verification)

        @app.post("/auth/verification/send")
        def verification_send(request: Request):
            return auth.verification_action(request.state.verification)

        @app.post("/auth/verification/verify")
        async def verification_verify(request: Request):
            try:
                body = await request.json()
            except ValueError:
                raise HTTPException(400, "Enter the email verification code") from None
            if not isinstance(body, dict) or set(body) != {"code"} or not isinstance(body["code"], str) or not re.fullmatch(r"[0-9]{6}", body["code"]):
                raise HTTPException(400, "Enter the six-digit email verification code")
            return await asyncio.to_thread(auth.verification_action, request.state.verification, body["code"])

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

    @app.get("/api/foundations/web-research")
    def web_research_foundation(request: Request):
        who(request)
        from .schemas import ResearchInput
        return {**WEB_FOUNDATION, "input_schema": ResearchInput.model_json_schema(),
                "report_skills": REPORT_SKILLS}

    @app.get("/api/build-options")
    def options(request: Request, foundation_id: str | None = None, model_id: str | None = None):
        persona = who(request)
        with store.tx() as db:
            foundations = [json.loads(r[0]) for r in db.select('foundations', columns=['body'])]
            foundations = [f for f in foundations if f["approved"]]
            choices = {"models": [], "tools": [], "skills": []}
            if catalog_mode == "live":
                try:
                    records = catalog_records(db)
                    status = builder_catalog.BINDING_MESSAGE
                except HTTPException as exc:
                    records, status = [], str(exc.detail) + "; configure owner-approved NATIVE_CATALOG_CONFIG"
                public = []
                for foundation in foundations:
                    compatible = builder_catalog.choices(db, persona, foundation, records, model_id)
                    public.append(builder_catalog.public_foundation(foundation, compatible))
                    if foundation['id'] == foundation_id:
                        choices = compatible
                return {"foundations": public, "choices": choices, "sample_dataset": SAMPLE_DATASET,
                        "policy": policy(db), "catalog_mode": "live", "integration_status": status,
                        "recalculate_on": ["foundation_id", "model_id"]}
            # Foundation compatibility IDs must not reveal hidden resources either.
            visible_ids = {json.loads(r[0])['id'] for r in db.select('components', columns=['body']) if visibility(json.loads(r[0]), persona)}
            foundations = [{**f, **{k: [cid for cid in f[k] if cid in visible_ids] for k in ('models', 'tools', 'skills')}} for f in foundations]
            if foundation_id == "web-research":
                return {"foundations": [*foundations, WEB_FOUNDATION], "choices": choices, "sample_dataset": SAMPLE_DATASET, "integration_status": "NOT_CONFIGURED"}
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
            return {"foundations": [*foundations, WEB_FOUNDATION], "choices": choices, "sample_dataset": SAMPLE_DATASET, "policy": policy(db), "catalog_revision": digest([foundations, choices]), "recalculate_on": ["foundation_id", "model_id"]}

    def save_definition(db, persona, data, agent_id=None):
        if persona["role"] != "business":
            raise HTTPException(403, "A business workspace membership is required to create agents")
        payload = data.model_dump(exclude={"base_version"}, exclude_none=True)
        if payload['foundation_id'] == WEB_FOUNDATION['id']:
            # Persist a source draft, never a fixture-executable or approved live binding.
            if (payload['foundation_version'] != WEB_FOUNDATION['version']
                    or payload['model_id'] or payload['tools'] or len(payload['skills']) != 1
                    or payload['skills'][0] not in REPORT_SKILLS):
                raise HTTPException(422, "Unconfigured research draft requires one packaged skill and no fabricated model/tool bindings")
            skill_id = payload['skills'][0]
            if payload['component_versions'] != {skill_id: REPORT_SKILLS[skill_id]['version']}:
                raise HTTPException(422, "Pinned packaged skill version required")
            foundation = WEB_FOUNDATION
            payload['integration_status'] = 'NOT_CONFIGURED'
            payload['skill_artifact'] = skill_binding(skill_id)
        elif catalog_mode == 'live':
            foundation = resource(db, 'foundations', payload['foundation_id'])
            previous = None
            if agent_id:
                existing = agent_access(db, persona, agent_id)
                previous = get_version(db, agent_id, existing['current_version'])
            payload['readiness'] = live_readiness(db, persona, payload, foundation, previous)
            payload['catalog_mode'] = 'live'
            # Return/store a safe manifest projection, never hidden binding IDs.
            foundation = builder_catalog.public_foundation(foundation, {'models': [], 'tools': [], 'skills': []})
        else:
            foundation = validate_current(db, persona, payload)
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
            definition = get_version(db, agent_id, agent["current_version"])
            if catalog_mode == 'live' and definition['foundation_id'] != 'web-research':
                foundation = resource(db, 'foundations', definition['foundation_id'])
                definition['readiness'] = live_readiness(db, who(request), definition, foundation, definition)
            return {**agent, "definition": definition, "versions": versions, "jobs": jobs}

    @app.post("/api/agents/{agent_id}/deploy", status_code=202)
    @app.post("/api/agents/{agent_id}/deploy-test", status_code=202)
    async def deploy(agent_id: str, data: Deploy, request: Request):
        if request.url.path.endswith("/deploy") and data.execution_mode != "live":
            raise HTTPException(422, "Explicit Deploy requires live execution mode")
        persona = who(request)
        with store.tx() as db:
            agent = agent_access(db, persona, agent_id)
            if agent["current_version"] != data.version:
                raise HTTPException(409, "Only the current definition can be tested")
            definition = get_version(db, agent_id, data.version)
            validate_current(db, persona, definition, live_execution=data.execution_mode == "live")
            if data.execution_mode == 'live' and (foundation_jobs is None or worker_enabled):
                raise HTTPException(503, 'LIVE_DISABLED: no fixture fallback')
            existing = db.select('jobs', where=[('agent', '=', agent_id), ('requester', '=', persona['id']), ('idem', '=', data.idempotency_key)]).fetchone()
            if existing:
                from .foundation_runs import get as get_run
                is_live = bool(get_run(db, 'foundation-run:' + existing['id']) or get_run(db, 'foundation-pending:' + existing['id']))
                if existing["version"] != data.version or is_live != (data.execution_mode == 'live'):
                    raise HTTPException(409, "Idempotency key already binds a different version")
                if is_live:
                    self_service.check_current(db, persona, definition,
                        get_foundation_record(db, 'foundation-approved:' + definition['digest']) or {})
                return {"job_id": existing["id"], "stage": existing["stage"], "reused": True}
            if db.select('jobs', where=[('agent', '=', agent_id), ('stage', "not_in", ['PASS','NEEDS_CHANGES','LIVE_PASS','BLOCKED'])]).fetchone():
                raise HTTPException(409, "A job already owns this agent; wait for completion")
            if db.select('jobs', count=True, where=[('stage', "not_in", ['PASS','NEEDS_CHANGES','LIVE_PASS','BLOCKED'])]).fetchone()[0] >= 8:
                raise HTTPException(429, "Local queue cap reached (8)")
            if db.select('jobs', count=True, where=[('requester', '=', persona['id']), ('created', '>', time.time() - 3600)]).fetchone()[0] >= 30:
                raise HTTPException(429, "Local budget cap: 30 jobs per identity per hour")
            job_id, now = uid(), time.time()
            db.insert('jobs', {'id': job_id, 'agent': agent_id, 'version': data.version, 'requester': persona['id'], 'idem': data.idempotency_key, 'stage': 'VALIDATING', 'created': now, 'updated': now, 'deadline': now + (900 if repository is not None else 60)})
            if data.execution_mode == 'live':
                foundation_jobs.enqueue(db, job_id, definition, persona, now + 900,
                    renew_authority=definition.get("catalog_mode") != "live")
                db.update('jobs', {'deadline': now + 900}, where=[('id', '=', job_id)])
            event(db, job_id, "VALIDATING", {"message": "Live deployment reserved" if data.execution_mode == "live" else "Local deploy/test accepted", "digest": definition["digest"], "mode": "live" if data.execution_mode == "live" else mode_label})
            audit(db, persona["id"], "deploy_test", agent_id, job_id)
            if repository is not None and hosted:
                from .hosted_auth import SESSION_COOKIE, sha
                db.insert("job_authority", {"id": job_id, "session_hash": sha(request.cookies.get(SESSION_COOKIE, ""))})
        request.app.state.wake.set()
        with store.tx() as db:
            stage = db.select('jobs', where=[('id', '=', job_id)]).fetchone()['stage']
        return {"job_id": job_id, "stage": stage, "reused": False}

    @app.get("/api/jobs/{job_id}")
    def job_result(job_id: str, request: Request):
        with store.tx() as db:
            row = db.select('jobs', where=[('id', '=', job_id)]).fetchone()
            if not row:
                raise HTTPException(404, "Job not found")
            agent = agent_access(db, who(request), row["agent"])
            events = [{**dict(r), "detail": json.loads(r["detail"])} for r in db.select('events', where=[('job', '=', job_id)], order='id')]
            result = json.loads(row["result"]) if row["result"] else None
            from .foundation_runs import get as get_run
            from .result_evidence import project
            live = get_run(db, 'foundation-run:' + job_id)
            if live:
                if (live['owner'], live['workspace'], live['agent'], live['version']) != (agent['owner'], agent['workspace'], agent['id'], row['version']):
                    raise HTTPException(404, "Job not found")
                # Protected detail only; never expose stored_input, raw logs or SDK payloads.
                projection = project(live, live.get('evidence'))
                result = {k: v for k, v in (result or {}).items() if k in ('passed', 'gate', 'mode', 'production_ready', 'failure')}
                result.update(mode='live', gate=result.get('gate', 'EVIDENCE_UNAVAILABLE'), result_evidence=projection,
                              runtime={k: live['runtime'][k] for k in ('runtime_version',) if live.get('runtime') and k in live['runtime']},
                              usage=projection['cost']['usage'], billing_estimate_usd=None, actual_invoice_usd=None)
                events = [{**e, 'detail': {'mode': 'live', 'stage': e['stage']}} for e in events]
            return {**dict(row), "result": result, "events": events, "current_version": agent["current_version"], "stale": row["version"] != agent["current_version"], "mode": mode_label}

    @app.post("/api/agents/{agent_id}/invoke")
    def invoke(agent_id: str, data: Invoke, request: Request):
        persona = who(request)
        with store.tx() as db:
            agent = agent_access(db, persona, agent_id)
            if data.version != agent["current_version"]:
                raise HTTPException(409, "Current version required; revise and retest")
            definition = get_version(db, agent_id, data.version)
            validate_current(db, persona, definition)
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
            if definition.get("foundation_id") == "web-research":
                raise HTTPException(503, "NOT_CONFIGURED: use the source-only research packager; fixture exports are forbidden")
            if definition.get("catalog_mode") == "live":
                raise HTTPException(503, "NOT_CONFIGURED: live agent archive export is not implemented; only the local fixture harness export is supported")
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

    @app.get("/api/catalog")
    def ai_catalog(request: Request, kind: str | None = None, q: str = ""):
        persona = who(request)
        with store.tx() as db:
            records = catalog_records(db)
            visible = {c['id'] for c in records if visibility(c, persona)}
            items = [p for c in records if (not c.get('parent_id') or c['parent_id'] in visible)
                     and (p := projection(db, persona, c)) is not None]
            # Per-user, DEDUPLICATED authorization summary for the authenticated
            # caller (Task 3, 哥哥 refinement 2). Computed from the caller's own
            # projections (has_grant is per persona+component), NOT the catalog
            # total. Granted / requestable / callable are DISTINCT categories:
            #   granted     = the caller actually holds a grant for this component,
            #   requestable = not granted, access can be requested (never counted
            #                 as granted),
            #   callable    = granted AND execution binding verified ("can invoke";
            #                 judged separately from granted).
            # Deduplicated by top-level component id so one grant is never counted
            # twice. Child tool operations and parent duplicates are excluded.
            from .model_access_summary import model_access_summary
            access_summary = model_access_summary(items)
            items = [p for p in items if (kind is None or p['kind'] == kind) and q.casefold() in (p['name'] + ' ' + p['description'] + ' ' + p['provider']).casefold()]
            return {"items": items, "count": sum(p['kind'] != 'tool' and not p.get('parent_id') for p in items), "mode": catalog_mode,
                    "access_summary": access_summary,
                    "agent_listing_implemented": catalog_mode == "live",
                    "connection_state": "connected" if catalog_mode == "live" else "fixture",
                    "native_connection_state": "connected" if catalog_mode == "live" else "NotConnected",
                    "sources": catalog_provider.source_status() if catalog_mode == 'live' and hasattr(catalog_provider, 'source_status') else {},
                    "revision": digest(items),
                    "execution_ready": False if catalog_mode == "live" else None}

    @app.get("/api/catalog/{component_id}/versions/{version}")
    @app.get("/api/catalog/{component_id}")
    def catalog_detail(component_id: str, request: Request, version: str | None = None):
        with store.tx() as db:
            item = catalog_resource(db, who(request), component_id)
            if version is not None and version != item['version']:
                raise HTTPException(404, "Capability not found")
            return projection(db, who(request), item)

    @app.get("/api/capabilities")
    def capabilities(request: Request):
        return ai_catalog(request)['items']

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
            component = catalog_resource(db, persona, data.component_id)
            if not projection(db, persona, component)['requestable']:
                raise HTTPException(403, "Capability is not requestable")
            if not data.reason.strip() or len(data.reason.strip()) < 5:
                raise HTTPException(422, "A substantive business purpose is required")
            if db.select('requests', where=[('requester', '=', persona['id']), ('component', '=', data.component_id), ('status', '=', 'PENDING')]).fetchone():
                raise HTTPException(409, "A request is already pending")
            if db.select('requests', count=True, where=[('requester', '=', persona['id']), ('created', '>', time.time() - 3600)]).fetchone()[0] >= 30:
                raise HTTPException(429, 'Access request budget: 30 per identity per hour')
            request_id = uid()
            db.insert('requests', {'id': request_id, 'requester': persona['id'], 'workspace': persona['workspace'], 'component': data.component_id, 'reason': data.reason, 'status': 'PENDING', 'decision': None, 'created': time.time()})
            db.insert('settings', {'key': 'request-version:' + request_id, 'body': json.dumps({'version': component['version']})})
            audit(db, persona["id"], "capability_requested", request_id, data.component_id)
        return {"id": request_id}

    # General platform requests: needs outside the AI Catalog. These are not
    # capability grants — admin handling records a response and never touches
    # grants, settings scopes or execution bindings.
    @app.get("/api/general-requests")
    def general_requests(request: Request):
        persona = who(request)
        with store.tx() as db:
            if persona["role"] == "admin":
                return [dict(r) for r in db.select('general_requests', order='created', descending=True)]
            return [dict(r) for r in db.select('general_requests', where=[('requester', '=', persona['id']), ('workspace', '=', persona['workspace'])], order='created', descending=True)]

    @app.post("/api/general-requests", status_code=201)
    def submit_general_request(data: GeneralRequest, request: Request):
        persona = who(request)
        if persona["role"] != "business":
            raise HTTPException(403, "Business identity required")
        with store.tx() as db:
            if db.select('general_requests', where=[('requester', '=', persona['id']), ('workspace', '=', persona['workspace']), ('summary', '=', data.summary), ('status', 'not_in', ('RESOLVED', 'CLOSED'))]).fetchone():
                raise HTTPException(409, "An identical request is already open; wait for the administrator response")
            if db.select('general_requests', count=True, where=[('requester', '=', persona['id']), ('created', '>', time.time() - 3600)]).fetchone()[0] >= 30:
                raise HTTPException(429, 'General request budget: 30 per identity per hour')
            request_id = uid()
            now = time.time()
            db.insert('general_requests', {'id': request_id, 'requester': persona['id'], 'workspace': persona['workspace'], 'summary': data.summary, 'details': data.details, 'status': 'SUBMITTED', 'resolution': None, 'created': now, 'updated': now})
            audit(db, persona["id"], "general_request_submitted", request_id, json.dumps({"workspace": persona["workspace"]}))
        return {"id": request_id}

    @app.post("/api/admin/general-requests/{request_id}/status")
    def handle_general_request(request_id: str, data: GeneralRequestStatus, request: Request):
        persona = who(request, True)
        with store.tx() as db:
            row = db.select('general_requests', where=[('id', '=', request_id)]).fetchone()
            if not row:
                raise HTTPException(404, "Request not found")
            if row["status"] in ("RESOLVED", "CLOSED"):
                raise HTTPException(409, "Request already resolved or closed")
            db.update('general_requests', {'status': data.status, 'resolution': data.note, 'updated': time.time()}, where=[('id', '=', request_id)])
            audit(db, persona["id"], "general_request_updated", request_id, json.dumps({"status": data.status, "workspace": row["workspace"]}))
        return {"ok": True, "notice": "Administrator response recorded; no access was granted or provisioned automatically"}

    @app.get("/api/admin/catalog")
    def admin_catalog(request: Request):
        who(request, True)
        with store.tx() as db:
            return {"foundations": [json.loads(r[0]) for r in db.select('foundations', columns=['body'])], "components": catalog_records(db), "grants": [dict(r) for r in db.select('grants')], "personas": principal_list(db), "policy": policy(db), "history": [dict(r) for r in db.select('catalog_history', order='id', descending=True, limit=100)]}

    @app.post("/api/admin/foundation-sources")
    def register_foundation(data: RegisterFoundation, request: Request):
        actor = who(request, True)
        if not hosted:
            raise HTTPException(403, "HOSTED_ADMIN_REVIEW_REQUIRED")
        platform = platform_metadata(data.config)
        with store.tx() as db:
            return register_source(db, actor, data, platform)

    @app.post("/api/internal/m0/foundation-approvals")
    def approve_foundation(data: ApproveFoundation, request: Request):
        actor = who(request, True)
        if not hosted or os.getenv("FOUNDATION_M0_PROBE_ENABLED") != "1":
            raise HTTPException(403, "INTERNAL_M0_PROBE_DISABLED")
        with store.tx() as db:
            definition = get_version(db, data.agent_id, data.version)
            source = get_foundation_record(db, 'foundation-source:'+definition['foundation_id'])
            if not source:
                raise HTTPException(409, "REGISTERED_SOURCE_REQUIRED")
            platform = platform_metadata(source['config'])
            return compile_approval(db, actor, data, principal(db, definition['owner']), platform)

    @app.post("/api/admin/foundation-artifacts/finalize")
    def finalize_foundation(data: FinalizeFoundation, request: Request):
        actor = who(request, True)
        if not hosted:
            raise HTTPException(403, "HOSTED_ADMIN_REVIEW_REQUIRED")
        with store.tx() as db:
            definition = get_version(db, data.agent_id, data.version)
            source = get_foundation_record(db, 'foundation-source:'+definition['foundation_id'])
            if not source:
                raise HTTPException(409, "REGISTERED_SOURCE_REQUIRED")
            return finalize_artifact(db, principal(db, actor['id']), data,
                principal(db, definition['owner']), platform_metadata(source['config']))

    @app.post("/api/admin/foundation-policies")
    def approve_self_service_policy(data: self_service.FoundationPolicy, request: Request):
        actor = who(request, True)
        if not hosted:
            raise HTTPException(403, "HOSTED_ADMIN_REVIEW_REQUIRED")
        with store.tx() as db:
            return self_service.approve_policy(db, actor, data)

    @app.post("/api/agents/{agent_id}/admission")
    def domain_admission(agent_id: str, data: self_service.AdmissionRequest, request: Request):
        with store.tx() as db:
            record = self_service.admit(db, who(request), agent_id, data.version,
                evaluator=getattr(foundation_jobs, 'policy_evaluator', None))
            return {"receipt": record['receipt'], "status": "ADMITTED_NOT_RELEASED"}

    @app.post("/api/agents/{agent_id}/exceptions", status_code=201)
    def domain_exception(agent_id: str, data: self_service.ExceptionRequest, request: Request):
        with store.tx() as db:
            return self_service.request_exception(db, who(request), agent_id, data)

    @app.post("/api/admin/domain-exceptions/{request_id}/decision")
    def domain_exception_decision(request_id: str, data: self_service.ExceptionDecision, request: Request):
        actor = who(request, True)
        if not hosted:
            raise HTTPException(403, "HOSTED_ADMIN_REVIEW_REQUIRED")
        with store.tx() as db:
            record = get_foundation_record(db, 'domain-exception:' + request_id)
            if not record:
                raise HTTPException(404, "Exception request not found")
            return self_service.decide_exception(db, actor, request_id, data, principal(db, record['creator']))

    @app.post("/api/admin/grants")
    def grant(data: Grant, request: Request):
        persona = who(request, True)
        with store.tx() as db:
            if catalog_mode == "live" and (not data.reason or len(data.reason.strip()) < 5):
                raise HTTPException(422, "A substantive grant or revocation reason is required")
            subject = principal(db, data.persona_id)
            component = catalog_resource(db, subject, data.component_id) if data.enabled else {'id': data.component_id, 'approved': True, 'external': False}
            if principal(db, data.persona_id)["role"] != "business":
                raise HTTPException(403, "Business identity required")
            if data.enabled and (not component["approved"] or component["external"] and not principal(db, data.persona_id)["external_allowed"]):
                raise HTTPException(403, "Approval or workspace data policy blocks this grant")
            if data.enabled:
                db.insert('grants', {'persona': data.persona_id, 'component': data.component_id}, ignore=True)
                db.insert('settings', {'key': grant_scope(subject, data.component_id), 'body': 'true'}, upsert=True)
            else:
                db.delete('grants', where=[('persona', '=', data.persona_id), ('component', '=', data.component_id)])
                db.delete('settings', where=[('key', '=', grant_scope(subject, data.component_id))])
            from .foundation_runs import get as epoch_get, put as epoch_put
            epoch_put(db, 'foundation-epoch', (epoch_get(db, 'foundation-epoch') or 0) + 1)
            audit(db, persona["id"], "grant" if data.enabled else "revoke", data.persona_id, json.dumps({"component": data.component_id, "reason": data.reason}))
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
            if len(data.reason.strip()) < 5:
                raise HTTPException(422, "A substantive decision reason is required")
            if data.approve:
                subject = principal(db, row['requester'])
                if subject['workspace'] != row['workspace']:
                    raise HTTPException(403, "Requester workspace changed; submit a new request")
                component = catalog_resource(db, subject, row['component'])
                pinned = db.select('settings', where=[('key', '=', 'request-version:' + request_id)]).fetchone()
                if pinned and json.loads(pinned['body'])['version'] != component['version']:
                    raise HTTPException(409, 'Capability version changed; reject and request the current version')
                if not component["approved"] or component["external"] and not principal(db, row["requester"])["external_allowed"]:
                    raise HTTPException(403, "Approval or data policy blocks this capability")
                db.insert('grants', {'persona': row['requester'], 'component': row['component']}, ignore=True)
                db.insert('settings', {'key': grant_scope(subject, row['component']), 'body': 'true'}, upsert=True)
            db.update('requests', {'status': 'APPROVED' if data.approve else 'REJECTED', 'decision': data.reason}, where=[('id', '=', request_id)])
            from .foundation_runs import get as epoch_get, put as epoch_put
            epoch_put(db, 'foundation-epoch', (epoch_get(db, 'foundation-epoch') or 0) + 1)
            audit(db, persona["id"], "request_decided", request_id, json.dumps({"decision": "approved" if data.approve else "rejected", "reason": data.reason, "workspace": row["workspace"]}))
        return {"ok": True, "notice": "Existing capability grant updated; no connector was created"}

    @app.post("/api/admin/catalog/{authority}/{resource_id}")
    def update_catalog(authority: str, resource_id: str, data: CatalogUpdate, request: Request):
        persona = who(request, True)
        if catalog_mode == "live":
            raise HTTPException(409, "Live provider approvals are operator-managed; this endpoint cannot onboard connectors")
        if authority not in ("components", "foundations"):
            raise HTTPException(404, "Unknown catalog authority")
        with store.tx() as db:
            item = resource(db, authority, resource_id)
            db.insert('catalog_history', {'resource': resource_id, 'body': json.dumps(item), 'created': time.time()})
            item["approved"] = data.approved
            item["version"] = str(int(item["version"]) + 1) if authority == "components" else f"1.0.{int(item['version'].split('.')[-1]) + 1}"
            db.update(authority, {'body': json.dumps(item)}, where=[('id', '=', resource_id)])
            from .foundation_runs import get as epoch_get, put as epoch_put
            epoch_put(db, 'foundation-epoch', (epoch_get(db, 'foundation-epoch') or 0) + 1)
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
            from .foundation_runs import get as epoch_get, put as epoch_put
            epoch_put(db, 'foundation-epoch', (epoch_get(db, 'foundation-epoch') or 0) + 1)
            audit(db, persona["id"], "policy_revision", "mandatory-gates", str(current["version"]))
        return current

    @app.get("/api/admin/audit")
    def audit_log(request: Request):
        who(request, True)
        with store.tx() as db:
            return [dict(r) for r in db.select('audit', order='id', descending=True, limit=300)]

    def step_job(job_id):
        from .foundation_runs import get as get_run
        with store.tx() as db:
            live_run = get_run(db, 'foundation-run:' + job_id) or get_run(db, 'foundation-pending:' + job_id)
        if live_run:
            try:
                if foundation_jobs is None:
                    raise HTTPException(503, 'LIVE_DISABLED')
                with store.tx() as db:
                    persona = principal(db, live_run['owner'])
                    definition = get_version(db, live_run['agent'], live_run['version'])
                    validate_current(db, persona, definition, live_execution=True)
                foundation_jobs.step(store, job_id)
            except Exception as exc:
                from foundation_harness.context import Denied
                code = str(exc) if isinstance(exc, Denied) else 'LIVE_STEP_FAILED'
                with store.tx() as db:
                    db.update('jobs', {'stage': 'BLOCKED', 'updated': time.time(),
                              'result': json.dumps({'passed': False, 'mode': 'live',
                                  'gate': code, 'failure': {'code': code, 'stage': 'LIVE_JOB'},
                                  'production_ready': False})}, where=[('id', '=', job_id)])
                    event(db, job_id, 'BLOCKED', {'code': code})
            return
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
                validate_current(db, persona, definition)
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
                jobs = [r["id"] for r in db.select('jobs', columns=['id'], where=[('stage', "not_in", ['PASS','NEEDS_CHANGES','LIVE_PASS','BLOCKED'])], order='created') if r["id"] not in running][:2 - len(running)]
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
                pending = db.select('jobs', where=[('stage', "not_in", ['PASS','NEEDS_CHANGES','LIVE_PASS','BLOCKED'])], limit=1).fetchone()
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
