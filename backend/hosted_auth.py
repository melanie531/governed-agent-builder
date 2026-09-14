"""Invite-only Cognito BFF: authorization code + PKCE, server-side tokens.

Only configured Cognito groups map to server-owned workspace policy. Explicitly
enrolled users can select an authorized role for their current session.
"""
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import time
from urllib.parse import urlencode, urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from functools import cached_property

import httpx
import jwt
from fastapi import HTTPException
from fastapi.responses import JSONResponse, RedirectResponse

SESSION_COOKIE = "__Host-gab_session"
PENDING_COOKIE = "__Host-gab_pending"
FLOW_COOKIE = "__Host-gab_flow"
GROUP_POLICY = {
    "studio-research": {"role": "business", "workspace": "research", "workspace_name": "Research studio", "external_allowed": False,
                        "grants": ["bedrock-claude", "bedrock-openai", "synthetic-search", "concise", "citations"]},
    "studio-operations": {"role": "business", "workspace": "operations", "workspace_name": "Operations desk", "external_allowed": False,
                          "grants": ["bedrock-claude", "synthetic-search", "concise"]},
    "studio-admin": {"role": "admin", "workspace": "platform", "workspace_name": "Platform governance", "external_allowed": False, "grants": []},
}


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


class HostedAuth:
    def __init__(self, store, public_url):
        self.store = store
        self.public_url = public_url.rstrip("/")
        region = os.environ.get("COGNITO_REGION", "")
        pool = os.environ.get("COGNITO_USER_POOL_ID", "")
        self.client_id = os.environ.get("COGNITO_CLIENT_ID", "")
        self.domain = os.environ.get("COGNITO_DOMAIN", "").rstrip("/")
        if not re.fullmatch(r"[a-z]{2}-[a-z]+-\d", region) or not re.fullmatch(re.escape(region) + r"_[A-Za-z0-9]+", pool):
            raise RuntimeError("HOSTED_PREVIEW requires an explicit Cognito region and user pool")
        if not re.fullmatch(r"[a-z0-9]{1,128}", self.client_id):
            raise RuntimeError("HOSTED_PREVIEW requires the isolated public Cognito client ID")
        d = urlparse(self.domain)
        if d.scheme != "https" or not re.fullmatch(r"[a-z0-9-]+\.auth\." + re.escape(region) + r"\.amazoncognito\.com", d.netloc) or d.path or d.query or d.fragment:
            raise RuntimeError("Use the isolated Cognito-managed HTTPS domain")
        self.issuer = f"https://cognito-idp.{region}.amazonaws.com/{pool}"
        self.keys = jwt.PyJWKClient(self.issuer + "/.well-known/jwks.json", cache_keys=False, lifespan=300, timeout=5)
        if not hasattr(store, "table"):
          with store.tx() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS oidc_flows(state_hash TEXT PRIMARY KEY, verifier TEXT NOT NULL, nonce TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS hosted_sessions(id_hash TEXT PRIMARY KEY, subject TEXT NOT NULL, access_token TEXT NOT NULL, csrf TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS principals(id TEXT PRIMARY KEY, body TEXT NOT NULL, expires REAL NOT NULL);
            ''')
            columns = {row[1] for row in db.execute("PRAGMA table_info(hosted_sessions)")}
            if "active_group" not in columns:
                db.execute("ALTER TABLE hosted_sessions ADD COLUMN active_group TEXT")

    def verify(self, token, purpose):
        if not isinstance(token, str) or len(token) > 16384:
            raise HTTPException(401, "Invalid authentication")
        try:
            key = self.keys.get_signing_key_from_jwt(token)
            options = {"require": ["exp", "iat", "sub", "iss", "token_use"], "verify_aud": purpose == "id"}
            claims = jwt.decode(token, key.key, algorithms=["RS256"], issuer=self.issuer,
                                audience=self.client_id if purpose == "id" else None, options=options)
            if claims.get("token_use") != purpose:
                raise ValueError("token purpose")
            if purpose == "access" and (claims.get("client_id") != self.client_id or "openid" not in claims.get("scope", "").split()):
                raise ValueError("client or scope")
            if not isinstance(claims["sub"], str) or not claims["sub"]:
                raise ValueError("subject")
            return claims
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise HTTPException(401, "Invalid or expired authentication") from None

    def approved_groups(self, claims):
        groups = claims.get("cognito:groups", [])
        if not isinstance(groups, list) or any(not isinstance(x, str) for x in groups):
            raise HTTPException(403, "No approved Studio membership")
        approved = [g for g in GROUP_POLICY if g in groups]
        # Multiple assignments require a separate, operator-managed enrollment.
        # Retain one canonical business workspace for grants and background jobs.
        switchable = ("studio-role-switcher" in groups and len(approved) == 2
                      and "studio-admin" in approved)
        if len(approved) != 1 and not switchable:
            raise HTTPException(403, "Exactly one approved Studio membership is required")
        return approved

    def membership(self, claims, selected=None):
        approved = self.approved_groups(claims)
        if selected is not None and selected not in approved:
            raise HTTPException(403, "This role is not authorized for your account")
        return GROUP_POLICY[selected or approved[0]]

    def role_options(self, request):
        claims, row = self.session(request)
        approved = self.approved_groups(claims)
        selected = row.get("active_group") or approved[0]
        self.membership(claims, selected)
        return [{"id": group, "label": "Platform Admin" if GROUP_POLICY[group]["role"] == "admin" else "Business User",
                 "selected": group == selected} for group in approved]

    def switch_role(self, request, group):
        claims, row = self.session(request)
        policy = self.membership(claims, group)
        with self.store.tx() as db:
            current = db.select("hosted_sessions", where=[("id_hash", "=", row["id_hash"]),
                                                        ("expires", ">", time.time())]).fetchone()
            if not current:
                raise HTTPException(401, "Sign in to Agent Studio")
            db.update("hosted_sessions", {"active_group": group}, where=[("id_hash", "=", row["id_hash"])])
            db.insert("audit", {"actor": claims["sub"], "action": "role_switched", "resource": group,
                               "detail": json.dumps({"role": policy["role"], "workspace": policy["workspace"]}),
                               "created": time.time()})
        return {"role": policy["role"]}

    def resolve(self, claims, name=None):
        policy = self.membership(claims)
        subject = claims["sub"]
        with self.store.tx() as db:
            old = db.select('principals', columns=['body'], where=[('id', '=', subject)]).fetchone()
            old_body = json.loads(old[0]) if old else None
            principal = {"id": subject, "name": name or (old_body or {}).get("name", "Studio member"), **{k: v for k, v in policy.items() if k != "grants"}}
            # Initial grants only once. Login never undoes an admin revocation.
            # Membership changes reset grants to the newly approved workspace.
            if old_body is None or old_body["workspace"] != principal["workspace"] or old_body["role"] != principal["role"]:
                db.delete('grants', where=[('persona', '=', subject)])
                for component in policy["grants"]:
                    db.insert("grants", {"persona": subject, "component": component}, ignore=True)
                if os.getenv("JOURNEY_ENABLED") == "1":
                    from .journey_catalog import records
                    from .live_catalog import grant_scope, visibility
                    for component in records(db):
                        if (principal["workspace"] in component.get("default_grant_workspaces", [])
                                and visibility(component, principal)):
                            db.insert("grants", {"persona": subject, "component": component["id"]}, ignore=True)
                            db.insert("settings", {"key": grant_scope(principal, component["id"]), "body": "true"}, upsert=True)
            db.insert('principals', {'id': subject, 'body': json.dumps(principal), 'expires': claims['exp']}, upsert=True)
        return principal

    def authenticate(self, request):
        # Retry the complete identity check after a rejected serializable read.
        # A deployment worker can advance the global fence during authentication;
        # that conflict must not become an API Gateway authorization denial.
        for attempt in range(6):
            try:
                return self._authenticate(request)
            except HTTPException as exc:
                if (exc.status_code != 409 or exc.detail != "Concurrent governance update; reload and retry"
                        or attempt == 5):
                    raise
                time.sleep(0.05 * (attempt + 1))

    def _authenticate(self, request):
        claims, row = self.session(request)
        # Persist the canonical membership only. An admin view must not reset
        # business grants or change authorization for an already-running job.
        principal = self.resolve(claims)
        policy = self.membership(claims, row.get("active_group"))
        return {**principal, **{k: v for k, v in policy.items() if k != "grants"}}, row["csrf"]

    def session(self, request):
        if PENDING_COOKIE in request.cookies:
            raise HTTPException(401, "Verify your email before entering Studio")
        cookie = request.cookies.get(SESSION_COOKIE, "")
        if not cookie:
            raise HTTPException(401, "Sign in to Agent Studio")
        with self.store.tx() as db:
            row = db.select('hosted_sessions', where=[('id_hash', '=', sha(cookie)), ('expires', '>', time.time())]).fetchone()
        if not row:
            raise HTTPException(401, "Sign in to Agent Studio")
        claims = self.verify(row["access_token"], "access")
        if claims["sub"] != row["subject"]:
            raise HTTPException(401, "Invalid authentication")
        from .qa_enrollment import approved
        approved(self, claims, required=cookie.startswith('qa.'))
        return claims, dict(row)

    def start(self, request=None):
        # Strict cookies may be absent on the cross-site callback. Revoke them
        # on the same-origin login start, as well as on callback.
        if request is not None:
            self.revoke_previous(request)
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        with self.store.tx() as db:
            db.delete('oidc_flows', where=[('expires', '<', time.time())])
            db.delete('hosted_sessions', where=[('expires', '<', time.time())])
            if db.select('oidc_flows', count=True).fetchone()[0] >= 100:
                raise HTTPException(429, "Sign-in capacity reached; retry shortly")
            db.insert('oidc_flows', {'state_hash': sha(state), 'verifier': verifier, 'nonce': nonce, 'expires': time.time() + 600})
        response = RedirectResponse(self.domain + "/oauth2/authorize?" + urlencode({"response_type": "code", "client_id": self.client_id,
            "redirect_uri": self.public_url + "/auth/callback", "scope": "openid email profile aws.cognito.signin.user.admin", "state": state, "nonce": nonce,
            "code_challenge": challenge, "code_challenge_method": "S256"}), status_code=302)
        response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="strict")
        response.delete_cookie(PENDING_COOKIE, secure=True, httponly=True, samesite="strict")
        response.set_cookie(FLOW_COOKIE, state, secure=True, httponly=True, samesite="lax", max_age=600)
        return response

    async def callback(self, request):
        self.revoke_previous(request)
        state = request.query_params.get("state", "")
        code = request.query_params.get("code", "")
        cookie = request.cookies.get(FLOW_COOKIE, "")
        # Only static categories are logged: never query values, cookies or tokens.
        reason = ("missing_state" if not state else "invalid_state_length" if len(state) > 256
                  else "missing_code" if not code else "invalid_code_length" if len(code) > 4096
                  else "missing_flow_cookie" if not cookie else "state_cookie_mismatch"
                  if not secrets.compare_digest(state, cookie) else None)
        if reason:
            logging.getLogger(__name__).warning("Sign-in callback rejected: %s", reason)
            raise HTTPException(400, "Invalid sign-in response; start again")
        request.state.matched_signin_flow = True
        with self.store.tx() as db:
            flow = db.select('oidc_flows', where=[('state_hash', '=', sha(state)), ('expires', '>', time.time())]).fetchone()
            db.delete('oidc_flows', where=[('state_hash', '=', sha(state))])
        if not flow:
            raise HTTPException(400, "Sign-in expired or already used")
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.post(self.domain + "/oauth2/token", data={"grant_type": "authorization_code", "client_id": self.client_id,
                    "redirect_uri": self.public_url + "/auth/callback", "code": code, "code_verifier": flow["verifier"]})
                if response.status_code != 200 or len(response.content) > 65536:
                    raise ValueError("token exchange")
                tokens = response.json()
            access = self.verify(tokens["access_token"], "access")
            identity = self.verify(tokens["id_token"], "id")
            if identity.get("nonce") != flow["nonce"] or identity["sub"] != access["sub"]:
                raise ValueError("identity binding")
        except (httpx.HTTPError, ValueError, KeyError):
            raise HTTPException(401, "Sign-in could not be verified; start again") from None
        email = identity.get("email")
        if not isinstance(email, str) or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 254:
            raise HTTPException(401, "Invalid authentication")
        self.membership(access)
        result = RedirectResponse(self.public_url + "/", status_code=303)
        result.delete_cookie(FLOW_COOKIE, secure=True, httponly=True, samesite="lax")
        result.delete_cookie(PENDING_COOKIE, secure=True, httponly=True, samesite="strict")
        from .qa_enrollment import approved
        qa = approved(self, access)
        if identity.get("email_verified") is True or qa:
            self.mint(result, access, tokens["access_token"], email, qa=qa)
        else:
            if "aws.cognito.signin.user.admin" not in access.get("scope", "").split():
                raise HTTPException(401, "Restart sign-in to verify email")
            cookie = secrets.token_urlsafe(32)
            expires = int(min(access["exp"], identity["exp"], time.time() + 600))
            self.verifications.put({"id": sha(cookie), "subject": access["sub"], "issuer": self.issuer,
                "client": self.client_id, "email": email, "access_token": tokens["access_token"],
                "csrf": secrets.token_urlsafe(32), "expires": expires, "sends": 0, "attempts": 0, "next_send": 0})
            result.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="strict")
            result.set_cookie(PENDING_COOKIE, cookie, secure=True, httponly=True, samesite="strict", max_age=max(0, expires-int(time.time())))
        return result

    @cached_property
    def verifications(self):
        from .verification_store import VerificationStore
        if hasattr(self.store, "table"):
            return VerificationStore(table_name=os.environ["VERIFICATION_TABLE"])
        return VerificationStore(path=self.store.path + ".verification.sqlite")

    def cognito(self):
        return boto3.client("cognito-idp", region_name=os.environ["COGNITO_REGION"],
            config=Config(connect_timeout=3, read_timeout=5, retries={"total_max_attempts": 1}))

    def revoke_previous(self, request):
        with self.store.tx() as db:
            db.delete('hosted_sessions', where=[('id_hash', '=', sha(request.cookies.get(SESSION_COOKIE, '')))])
        if request.cookies.get(PENDING_COOKIE):
            self.verifications.delete(sha(request.cookies[PENDING_COOKIE]))

    def mint(self, result, access, token, email, qa=False):
        if qa:
            from .qa_enrollment import approved
            approved(self, access, required=True)
        self.resolve(access, email)
        cookie, csrf = ('qa.' if qa else '') + secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.store.tx() as db:
            db.insert('hosted_sessions', {'id_hash': sha(cookie), 'subject': access['sub'], 'access_token': token, 'csrf': csrf, 'expires': min(access['exp'], time.time() + 3600)})
        result.set_cookie(SESSION_COOKIE, cookie, secure=True, httponly=True, samesite="strict", max_age=max(0, int(min(access["exp"] - time.time(), 3600))))

    def pending(self, request):
        cookie = request.cookies.get(PENDING_COOKIE, "")
        if not cookie or len(cookie) > 256:
            raise HTTPException(401, "Verification required; start sign-in again")
        row = self.verifications.get(sha(cookie))
        self.pending_claims(row)
        return row

    def pending_claims(self, row):
        access = self.verify(row["access_token"], "access")
        if (row["expires"] <= time.time() or row["issuer"] != self.issuer or row["client"] != self.client_id
                or access["sub"] != row["subject"] or "aws.cognito.signin.user.admin" not in access.get("scope", "").split()):
            raise HTTPException(401, "Invalid or expired verification")
        self.membership(access)
        return access

    def verification_status(self, row):
        return {"state": "PENDING_EMAIL_VERIFICATION", "csrf": row["csrf"], "expires": int(row["expires"]),
            "next_send": int(row["next_send"]), "sends_remaining": max(0, 3-int(row["sends"]))}

    def current_user(self, client, row):
        attrs = client.get_user(AccessToken=row["access_token"]).get("UserAttributes", [])
        values = {a["Name"]: a["Value"] for a in attrs}
        if values.get("sub") != row["subject"] or values.get("email") != row["email"]:
            raise HTTPException(401, "Verification identity changed; start sign-in again")
        return values

    def verification_action(self, row, code=None):
        # Reserve atomically before any Cognito I/O, including GetUser.
        row = self.verifications.reserve(row["id"], "sends" if code is None else "attempts")
        self.pending_claims(row)
        try:
            client = self.cognito()
            self.current_user(client, row)
            if code is None:
                delivery = client.get_user_attribute_verification_code(AccessToken=row["access_token"], AttributeName="email").get("CodeDeliveryDetails", {})
                if delivery.get("AttributeName") != "email" or delivery.get("DeliveryMedium") != "EMAIL":
                    raise HTTPException(400, "Email delivery could not be confirmed")
                return JSONResponse({"sent": True, **self.verification_status(row)})
            client.verify_user_attribute(AccessToken=row["access_token"], AttributeName="email", Code=code)
            if self.current_user(client, row).get("email_verified") != "true":
                raise HTTPException(401, "Email verification could not be confirmed")
        except (ClientError, BotoCoreError) as exc:
            safe = {"CodeDeliveryFailureException", "InvalidEmailRoleAccessPolicyException", "LimitExceededException", "TooManyRequestsException", "InvalidLambdaResponseException", "UserLambdaValidationException", "UnexpectedLambdaException"}
            name = exc.response.get("Error", {}).get("Code") if isinstance(exc, ClientError) else None
            raise HTTPException(400, "Verification could not be completed" + (": " + name if name in safe else "")) from None
        self.verifications.consume(row["id"])
        access = self.pending_claims(row)
        result = JSONResponse({"verified": True})
        self.mint(result, access, row["access_token"], row["email"])
        result.delete_cookie(PENDING_COOKIE, secure=True, httponly=True, samesite="strict")
        return result

    def logout(self, request):
        with self.store.tx() as db:
            db.delete('hosted_sessions', where=[('id_hash', '=', sha(request.cookies.get(SESSION_COOKIE, '')))])
        result = JSONResponse({"logout_url": self.domain + "/logout?" + urlencode({"client_id": self.client_id, "logout_uri": self.public_url + "/"})})
        result.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="strict")
        return result
