"""Invite-only Cognito BFF: authorization code + PKCE, server-side tokens.

Only configured Cognito groups map to server-owned workspace policy. The browser
never chooses a user or role. No provider credentials are required by this module.
"""
import base64
import hashlib
import json
import os
import re
import secrets
import time
from urllib.parse import urlencode, urlparse

import httpx
import jwt
from fastapi import HTTPException
from fastapi.responses import JSONResponse, RedirectResponse

SESSION_COOKIE = "__Host-gab_session"
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

    def resolve(self, claims, name=None):
        groups = claims.get("cognito:groups", [])
        if not isinstance(groups, list) or any(not isinstance(x, str) for x in groups):
            raise HTTPException(403, "No approved Studio membership")
        approved = [g for g in groups if g in GROUP_POLICY]
        # Ambiguous assignments fail closed. Adding workspace switching requires
        # explicit per-workspace authorization, not a browser-selected role.
        if len(approved) != 1:
            raise HTTPException(403, "Exactly one approved Studio membership is required")
        policy = GROUP_POLICY[approved[0]]
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
            db.insert('principals', {'id': subject, 'body': json.dumps(principal), 'expires': claims['exp']}, upsert=True)
        return principal

    def authenticate(self, request):
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
        return self.resolve(claims), row["csrf"]

    def start(self):
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        with self.store.tx() as db:
            db.delete('oidc_flows', where=[('expires', '<', time.time())])
            db.delete('hosted_sessions', where=[('expires', '<', time.time())])
            if db.select('oidc_flows', count=True).fetchone()[0] >= 100:
                raise HTTPException(429, "Sign-in capacity reached; retry shortly")
            db.insert('oidc_flows', {'state_hash': sha(state), 'verifier': verifier, 'nonce': nonce, 'expires': time.time() + 600})
        response = RedirectResponse(self.domain + "/oauth2/authorize?" + urlencode({"response_type": "code", "client_id": self.client_id,
            "redirect_uri": self.public_url + "/auth/callback", "scope": "openid email profile", "state": state, "nonce": nonce,
            "code_challenge": challenge, "code_challenge_method": "S256"}), status_code=302)
        response.set_cookie(FLOW_COOKIE, state, secure=True, httponly=True, samesite="lax", max_age=600)
        return response

    async def callback(self, request):
        state = request.query_params.get("state", "")
        code = request.query_params.get("code", "")
        if not state or len(state) > 256 or not code or len(code) > 4096 or not secrets.compare_digest(state, request.cookies.get(FLOW_COOKIE, "")):
            raise HTTPException(400, "Invalid sign-in response; start again")
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
            if identity.get("nonce") != flow["nonce"] or identity["sub"] != access["sub"] or not identity.get("email_verified"):
                raise ValueError("identity binding")
        except (httpx.HTTPError, ValueError, KeyError):
            raise HTTPException(401, "Sign-in could not be verified; start again") from None
        self.resolve(access, identity.get("email", "Studio member"))
        cookie, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.store.tx() as db:
            db.delete('hosted_sessions', where=[('id_hash', '=', sha(request.cookies.get(SESSION_COOKIE, '')))])
            db.insert('hosted_sessions', {'id_hash': sha(cookie), 'subject': access['sub'], 'access_token': tokens['access_token'], 'csrf': csrf, 'expires': min(access['exp'], time.time() + 3600)})
        result = RedirectResponse(self.public_url + "/", status_code=303)
        result.delete_cookie(FLOW_COOKIE, secure=True, httponly=True, samesite="lax")
        result.set_cookie(SESSION_COOKIE, cookie, secure=True, httponly=True, samesite="strict", max_age=max(0, int(min(access["exp"] - time.time(), 3600))))
        return result

    def logout(self, request):
        with self.store.tx() as db:
            db.delete('hosted_sessions', where=[('id_hash', '=', sha(request.cookies.get(SESSION_COOKIE, '')))])
        result = JSONResponse({"logout_url": self.domain + "/logout?" + urlencode({"client_id": self.client_id, "logout_uri": self.public_url + "/"})})
        result.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="strict")
        return result
