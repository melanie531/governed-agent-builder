"""Live wiring for the ALPR reader: explicit opt-in, fail closed, no bootstrap.

This module prepares (but does not perform) live Snowflake access:

- ``build_reader(config)`` is the single entrypoint hook. Default behaviour
  is unchanged platform behaviour: no config -> AlprLiveConfigError (fail
  closed). ``mode="offline"`` requires an explicitly injected transport
  (tests pass the fixture; there is NO silent fixture fallback here).
  ``mode="live"`` additionally requires ``live_confirmed=True`` plus a
  complete config, and refuses any bootstrap identity
  (GAB_BOOTSTRAP user / GAB_BOOTSTRAP_ROLE): the real-run gate must never
  use the bootstrap principal. The reader role is pinned to
  GAB_QUERY_READONLY.
- ``LiveSqlApiTransport`` implements the same submit/fetch contract as the
  offline fixture against the Snowflake SQL API v2 (POST /api/v2/statements,
  GET /api/v2/statements/<handle>), authenticating with key-pair JWT built
  lazily on first request — config validation never reads the private key,
  and the key path/identity are never logged or included in errors.
  Note on session semantics (official SQL API docs): each request is its own
  session; the reader therefore sends role pinning, USE SECONDARY ROLES NONE,
  verification and the data SELECT in ONE multi-statement request.
- ``LIVE_PROBE_PLAN`` is the prepared acceptance probe set for the real run:
  positive SELECTs on the four approved views, negative zero-row (LIMIT 0)
  SELECTs on base tables and the gold table expecting denial. No write
  probes of any kind. Executing it requires an admin to first run
  ``GRANT ROLE GAB_QUERY_READONLY TO USER GAB_BOOTSTRAP`` (or provision a
  dedicated query user) and an explicit live invocation; this slice does not
  log in.
"""
import base64
import hashlib
import json
import time

from backend.alpr_reader import AlprReadOnlyReader, READER_ROLE

_REQUIRED_LIVE_FIELDS = ("account_url", "user", "role", "warehouse", "private_key_path")
_APPROVED = "GAB_DEMO_DB.ALPR_APPROVED"
_BASE = "GAB_DEMO_DB.ALPR_DEMO"


class AlprLiveConfigError(Exception):
    """Configuration rejected; the reader fails closed."""


def _is_bootstrap(name):
    return "BOOTSTRAP" in str(name).upper()


def build_reader(config, *, transport=None, **reader_kwargs):
    """Single entrypoint hook: returns a fail-closed reader or raises.

    Platform default behaviour is preserved: nothing selects live implicitly.
    """
    mode = (config or {}).get("mode")
    if mode not in ("offline", "live"):
        raise AlprLiveConfigError("mode must be explicitly 'offline' or 'live'; "
                                  "no default execution path exists")
    if mode == "offline":
        if transport is None:
            raise AlprLiveConfigError("offline mode requires an injected transport; "
                                      "there is no implicit fixture fallback")
        return AlprReadOnlyReader(transport, **reader_kwargs)

    # live
    if config.get("live_confirmed") is not True:
        raise AlprLiveConfigError("live mode requires live_confirmed=True (explicit opt-in)")
    for field in _REQUIRED_LIVE_FIELDS:
        if not config.get(field):
            raise AlprLiveConfigError(f"live config missing required field: {field}")
    if _is_bootstrap(config["user"]) or _is_bootstrap(config["role"]):
        raise AlprLiveConfigError("bootstrap identities are forbidden for the live reader")
    if config["role"] != READER_ROLE:
        raise AlprLiveConfigError(f"live role must be {READER_ROLE}")
    if transport is not None:
        raise AlprLiveConfigError("live mode builds its own transport; injection forbidden")
    return AlprReadOnlyReader(LiveSqlApiTransport(config), **reader_kwargs)


class LiveSqlApiTransport:
    """Snowflake SQL API v2 transport with lazy key-pair JWT auth.

    Same submit/fetch contract as the offline fixture. The private key is
    read only when the first request is issued — never during config
    validation — and no identity/secret material is logged or embedded in
    exceptions.
    """

    def __init__(self, config):
        for field in _REQUIRED_LIVE_FIELDS:
            if not config.get(field):
                raise AlprLiveConfigError(f"live config missing required field: {field}")
        if _is_bootstrap(config["user"]) or _is_bootstrap(config["role"]):
            raise AlprLiveConfigError("bootstrap identities are forbidden")
        self._config = dict(config)
        self._base = config["account_url"].rstrip("/")
        self._key_loaded = False
        self._key = None
        self.calls = 0

    # -- auth (lazy) ------------------------------------------------
    def _load_key(self):
        if not self._key_loaded:
            from cryptography.hazmat.primitives import serialization
            with open(self._config["private_key_path"], "rb") as handle:
                self._key = serialization.load_pem_private_key(handle.read(), password=None)
            self._key_loaded = True
        return self._key

    def _jwt(self):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        key = self._load_key()
        if not isinstance(key, rsa.RSAPrivateKey):
            raise AlprLiveConfigError("key-pair JWT requires an RSA private key")
        pub = key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(pub).digest()).decode()
        account = self._base.split("//")[1].split(".")[0].upper()
        subject = f"{account}.{self._config['user'].upper()}"
        now = int(time.time())
        def b64(raw):
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        signing_input = (b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()) + "."
                         + b64(json.dumps({"iss": f"{subject}.{fingerprint}", "sub": subject,
                                           "iat": now, "exp": now + 300}).encode()))
        signature = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
        return signing_input + "." + b64(signature)

    def _headers(self):
        return {"Authorization": "Bearer " + self._jwt(),
                "X-Snowflake-Authorization-Token-Type": "KEYPAIR_JWT",
                "User-Agent": "gab-alpr-readonly/1.0"}

    # -- transport contract ------------------------------------------
    def submit(self, body):
        import requests
        self.calls += 1
        payload = dict(body)
        payload.setdefault("warehouse", self._config["warehouse"])
        response = requests.post(self._base + "/api/v2/statements",
                                 headers=self._headers(), json=payload, timeout=35)
        document = response.json()
        if response.status_code not in (200, 202):
            raise AlprLiveConfigError(
                f"SQL API rejected the request (HTTP {response.status_code}, "
                f"code {document.get('code')})")
        if "statementHandles" in document:
            return {"statementHandles": document["statementHandles"]}
        return {"statementHandles": [document["statementHandle"]]}

    def fetch(self, handle):
        import requests
        self.calls += 1
        response = requests.get(self._base + "/api/v2/statements/" + handle,
                                headers=self._headers(), timeout=30)
        for _ in range(15):
            if response.status_code != 202:
                break
            time.sleep(2)
            response = requests.get(self._base + "/api/v2/statements/" + handle,
                                    headers=self._headers(), timeout=30)
        document = response.json()
        if response.status_code != 200:
            raise AlprLiveConfigError(
                f"SQL API statement failed (HTTP {response.status_code}, "
                f"code {document.get('code')})")
        return {"resultSetMetaData": document.get("resultSetMetaData", {}),
                "data": document.get("data", [])}


def _probe(object_name, sql, expect):
    return {"object": object_name, "sql": sql, "expect": expect,
            "role": READER_ROLE, "write": False}


# Prepared live acceptance probes. NOT executed by this slice.
LIVE_PROBE_PLAN = tuple(
    [_probe(f"{_APPROVED}.{view}",
            f"SELECT CASE_ID FROM {_APPROVED}.{view} WHERE CASE_ID = 'CASE-001' LIMIT 5",
            "rows_allowed")
     for view in ("ACCOUNT_VEHICLE", "BILLING_NOTICE", "REMEDIATION", "POLICY")]
    + [_probe(f"{_BASE}.{table}", f"SELECT 1 FROM {_BASE}.{table} LIMIT 0", "denied")
       for table in ("CASES", "PASSAGE_EVENTS", "OWNERSHIP_HISTORY", "BILLING_NOTICES",
                     "PAYMENTS", "REMEDIATION_HISTORY", "POLICY_VERSIONS",
                     "EXPECTED_CASE_RESULTS")]
)
