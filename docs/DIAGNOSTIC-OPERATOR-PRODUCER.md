# Diagnostic operator producer: exact hosted calls

Target only: **https://de32ssfw7gsad.cloudfront.net** (Agent Studio).
This change is code, tests and documentation only. It does not deploy, invoke a
model, write IAM, initialize state or create budget approval records. The parent
operator already approved US$1; this API references evidence of that envelope,
not a new budget approval. Never submit the synthetic test fixture to production.

## Deployment seam for the separate cloud writer

Apply this producer commit on top of consumer compatibility commit
`1fdbe52ad05b2be3a51a740cd3bf8a3ed1f19ca4`. That commit owns
`backend/diagnostic_capture.py`: preserve both ASCII stored-definition hashing and
READY endpoints with absent `targetVersion`. This producer does not edit it.

Changed application paths: `backend/app.py` routes and
`backend/diagnostic_approval.py` implementation. Existing
`scripts/serverless_package.py` copies all of `backend`, `foundation_harness`,
`runtime`, and the required `scripts/package_foundation.py` and
`scripts/opus_capture_ticket.py`; no package allowlist change is needed.
`source_digest()` must run against those packaged source bytes, not unrelated
checkout bytes.

The existing app-stack logical resource **Business**, handler
`backend.serverless.api_handler`, must receive the new application artifact.
The existing authenticated `ANY /api/{proxy+}` route already dispatches these
paths through the Cognito authorizer and application authentication/CSRF checks.
Keep its existing `STATE_TABLE`, `HOSTED_PREVIEW`, `PUBLIC_URL`, Cognito settings,
role and permissions unchanged. No new route, table, role or infrastructure.
The independent consumer service is **DiagnosticCaptureExchange**, handler
`backend.serverless.diagnostic_capture_exchange_handler`; its compatibility
update/activation remains the parent cloud writer's work. Do not enable it as a
side effect of deploying the producer. Do not change Auth, Authorizer, Worker,
Dispatcher or FoundationExchange permissions or state.

Second existing admin availability remains an unverified, separate operator
prerequisite. Do not create reviewers or weaken independent review to resolve it.
The parent owns cloud deployment and the existing US$1 approval; this code fix
neither spends that approval nor authorizes Runtime recreation.

These calls are callable only **after** the Business application code deployment.
This delivery makes no claim that the endpoints have been deployed or live-tested.

## Authentication and calls

Use two distinct, existing hosted **studio-admin** identities. The stored agent
owner must remain a current business principal in the agent's workspace. Both
submitter and reviewer must be distinct from that owner, and reviewer must differ
from submitter. No JSON identity field, header role, local demo admin or approval
boolean can substitute for these identities.

In the target site's authenticated browser context, obtain CSRF via `GET /api/me`.
The existing HttpOnly `__Host-gab_session` cookie is sent by the browser. Never
copy cookies, tokens, evidence or private readbacks into chat, command arguments,
logs or documentation. POSTs require same-origin and `X-CSRF-Token`; all calls
require the current hosted session. API responses are private/no-store.

1. **Submitter:** `POST /api/admin/diagnostic-capture/submissions`
   with the exact JSON envelope below. Returns HTTP 201:
   `{"candidate_ref":"<64 lowercase hex>","status":"SUBMITTED_NOT_AUTHORITY"}`.
   Submission writes an immutable candidate and submission audit only. It is not
   authority and does not reserve money or invoke anything.
2. **Independent reviewer:**
   `GET /api/admin/diagnostic-capture/submissions/{candidate_ref}`.
   Returns `{evidence, submitter, submitted_at}`. Inspect this exact stored
   evidence, not a replacement payload. Its canonical digest is `candidate_ref`.
3. **Independent reviewer:** `POST /api/admin/diagnostic-capture/reviews`
   with exactly
   `{"candidate_ref":"<returned ref>","reason":"<substantive independent review reason>"}`.
   Returns HTTP 200:
   `{"capture_ref":"<authority digest>","candidate_ref":"<ref>","status":"REVIEWED_NOT_INVOKED"}`.
   This is the real audited diagnostic authority reference to hand to the parent
   operator. It is not a product approval, Ready flag or successful model capture.

No `approved`, `reviewer`, `role`, `owner` or `submitter` fields are accepted in
these request envelopes. The server derives identity and review timestamps.
Reasons must be 10–1000 characters, including at least 10 after trimming.
Denials return 401/403 for authentication/authorization, 409 for semantic or
concurrent-state failures, 413 for oversized bodies, 422 for request schema violations. Missing candidate
reads return 404. A duplicate review is rejected, not overwritten.

## Exact submission envelope and digest order

Top-level fields (all required, no extras):
`authority`, `runtime`, `pricing`, `isolation_source`, `price_sources`, `manifest`,
`runtime_readback`, `endpoint_readback`, `budget`, `reason`.
The existing HTTP middleware caps the whole request body at 65536 bytes; keep
submission JSON below that limit. The server helper also independently caps
canonical evidence at 128000 bytes for non-HTTP composition.

Except for stored definitions, `D(x)` means
`foundation_harness.config.digest(x)`: SHA-256 over sorted compact JSON,
`ensure_ascii=False`, `allow_nan=False`. Definition digest is the **existing
stored value**, verified using ASCII-escaped JSON over the definition with its
`digest` field removed. Never rewrite stored definitions to match a manifest.

Build in this order so there is no circular hash dependency:

* `request`: exactly `{endpoint, system, prompt, max_tokens}`. Use the existing
  reviewed dedicated us-west-2 Gateway `/bedrockrt/v1/messages` URL, exact prompt
  and system strings and bounded integer max_tokens accepted by the consumer.
* `manifest`: use the **unchanged existing** `gab-diagnostic-build-manifest-v1`
  object, not a newly constructed request manifest. Its exact 20 fields are
  `{schema, purpose, release_sha, region, entrypoint, exchange_endpoint,
  source_files, dependency_lock_sha256, dependency_source_zip_sha256,
  definition_digest, agent_id, agent_version, requested_model, max_output_tokens,
  stream, thinking, tools, transport_retries, activation, production_admission}`.
  The producer checks the exact 17-file name/hash map against its packaged bytes,
  pins the retained `ca333d0` release and dependency lock/source-ZIP digests, and
  binds the stored agent/version/definition. Fixed settings are us-west-2,
  `runtime.diagnostic_capture:create_app`, `us.anthropic.claude-opus-5`, 256 output
  tokens, stream false, thinking disabled, no tools/retries, activation
  `disabled-pending-authenticated-authority`, production admission false.
  Exchange must be the us-west-2 execute-api `/internal/diagnostic/capture` URL.
  The four-field unversioned manifest is rejected. There is no `request_digest`
  in this build schema: bind the exact request separately through authority and
  pricing below. Preserve `D(manifest)` already bound into the existing Runtime;
  do not rewrite the manifest/settings/environment or recreate Runtime.
  Release/dependency pins describe build inputs, not a hash of the final Runtime
  ZIP. The independently reviewed immutable S3 artifact and Runtime readback bind
  that artifact; this producer does not fetch ZIPs, dependencies or cloud facts.
* `isolation_source`: exactly `{source, role, runtime_arn, runtime_version,
  trust_policy, invoke_policy, attached_runtime_versions}`. Policies are the
  existing actual reviewed readback objects, not boolean attestations.
  `attached_runtime_versions` must be exactly one object
  `{runtime_arn, runtime_version}` matching the dedicated runtime. `source` is a
  nonblank evidence location. The independent reviewer must verify these policies
  establish isolation; this producer does not fetch or modify IAM.
* `runtime`: exactly `{role, runtime_id, runtime_arn, runtime_version,
  endpoint_name, manifest_digest, definition_digest, model_endpoint, readback,
  isolation_evidence}`. Use the actual owned READY version `"1"`, not a new runtime.
  `manifest_digest=D(manifest)` and `model_endpoint=request.endpoint`.
  `readback` has exactly `{roleArn, agentRuntimeArtifact, networkConfiguration,
  environmentVariables}`. Require matching role, VPC network, immutable S3
  artifact `bucket/prefix/versionId`, code runtime `PYTHON_3_13` with
  `entryPoint=["main.py"]`, and the actual environment's
  `DEFINITION_DIGEST`/`MANIFEST_DIGEST` bindings. No altered deployment is authorized
  to make a candidate pass.
  `isolation_evidence` is exactly `{source, sha256, role, runtime_arn,
  runtime_version, invoke_policy_sha256}`, with `sha256=D(isolation_source)` and
  `invoke_policy_sha256=D(isolation_source.invoke_policy)`.
* `price_sources`: exactly these ten service keys: `model_input`, `model_output`,
  `gateway_policy`, `runtime_lifetime`, `storage`, `telemetry`, `network`,
  `state_store`, `artifact`, `authentication`. Each value has exactly
  `{source, service, rate_usd, quantity_bound, usage_bound, retention_seconds,
  source_excerpt}`. `service` matches its key; `source` uses HTTPS; usage bound and
  excerpt are nonblank; retention is integer 0..31536000. Rate and quantity are
  nonnegative finite decimal **strings**, never numbers, null or booleans.
* `pricing`: exactly `{request_digest, runtime_ref, reservation_usd, costs, evidence}`.
  `runtime_ref=D(runtime)`, request digest as above, `reservation_usd="0.50"`.
  `costs` and `evidence` have exactly the same ten service keys.
  For each service, `evidence[service]` is exactly
  `{source, sha256, usage_bound, retention_seconds}`, copied from the matching
  source with `sha256=D(price_sources[service])`.
  `costs[service]` is exactly `{usd, basis}` with `basis=D(evidence[service])` and
  decimal-string `usd=rate_usd * quantity_bound`, checked with decimal arithmetic.
  Total must be positive and no more than US$0.50; zero-priced services still need
  sourced evidence. Unknown pricing fails closed.
* `authority`: exactly `{purpose, agent_id, version, definition_digest, request,
  request_digest, runtime_ref, pricing_ref, expires_at, epoch, policy_digest}`.
  Bind the current stored agent/version/definition, `D(request)`, `D(runtime)` and
  `D(pricing)`. `version` is an integer; `epoch` is a nonnegative integer, not a
  boolean, matching current `foundation-epoch` (absent means 0).
  `policy_digest=D(current protected policy)`. Obtain these from the parent's
  current read-only state metadata, not invented values. Expiry is a finite Unix
  numeric timestamp, strictly future and at most one hour away and no later than
  the current submitting/reviewing admin membership expiry. Consumption is also
  bounded by owner/reviewer membership expiry. Expired candidates need fresh
  evidence; stored ones cannot be edited.
* `runtime_readback`: actual GetAgentRuntime response: READY, exact ARN/version,
  matching all four `runtime.readback` fields, and
  `lifecycleConfiguration={idleRuntimeSessionTimeout:60,maxLifetime:60}`.
* `endpoint_readback`: actual GetAgentRuntimeEndpoint response: READY, exact
  `name`, `agentRuntimeArn` and `liveVersion`. Absent `targetVersion` is valid;
  present null or a different version is rejected.
* `budget`: exactly `{source, sha256, source_excerpt, total_usd, capture_usd,
  studio_usd}`. Reference the parent's existing approval: `total_usd="1"`,
  `capture_usd="0.50"`, `studio_usd="0.50"`; source and excerpt nonblank.
  Here `sha256` is SHA-256 of the **exact UTF-8 excerpt bytes**, not `D(excerpt)`.
  This evidence cannot create budget authority. The review decision carries the
  evidence digest to prevent a second capture from reusing that same envelope.

Readbacks and price sources remain admin attestations reviewed independently,
not cloud facts fetched by this API. Consumer runtime/endpoint readback is checked
again before reserving. If deployed metadata does not satisfy a required binding,
report the exact mismatch to the parent; do not rewrite evidence, definition,
permissions or runtime configuration here.

## Durable effects and safety evidence

Submission: `diagnostic-capture:candidate:<ref>` plus
`diagnostic_capture_submitted` audit.
Review atomically writes content-addressed `authority`, `runtime`, `pricing`,
`isolation-source`, `price-source` records; three immutable `review:<digest>`
receipts and `diagnostic_capture_reviewed` audits; immutable
`decision:<candidate_ref>` and `diagnostic_capture_published` audit. Sources may be
reused only unchanged. Revalidation covers current principals, owner/version,
grants, policy, epoch, evidence, expiry and actual consumer admission logic.
Any failure rolls back all producer writes; DynamoDB's revision CAS permits only
one concurrent publisher. No budget authority, tickets, grants, agent versions,
product approval or inference records are created by these endpoints.

Focused tests are `tests/test_diagnostic_approval.py` plus existing diagnostic
consumer, exchange, compatibility and release tests. They use signed synthetic
JWTs, Moto DynamoDB and temporary SQLite, never production records.

Well-Architected scope: security (hosted identity, independent review, CSRF,
immutable audit); reliability (atomic rollback and CAS); performance (bounded
candidate, existing low-throughput serializable store); cost (reference existing
US$1 envelope, no inference); operations (explicit producer/consumer deployment
seams); sustainability (reuse existing services, no new infrastructure). Remaining
security limit: authenticity of submitted cloud/pricing evidence requires human
independent review; hashes bind content but are not external signatures.

## Local verification receipt (2026-09-14)

Existing project `.venv/bin/python -m pytest -q -rs` over
`tests/test_diagnostic_approval.py tests/test_diagnostic_live_compat.py
 tests/test_diagnostic_capture.py tests/test_diagnostic_exchange.py
 tests/test_diagnostic_release.py tests/test_catalog_response_compatibility.py
 tests/test_discovery_api_catalog.py`: **291 passed, 4 skipped**, 45.97 seconds.
The producer fixture now uses the deployed 20-field schema with synthetic
identities. Regressions cover rehashed build/source-map drift, legacy-schema
rejection, artifact/runtime changes, exact request/pricing binding and source
changes between submission and review. A separate local read-only check accepted
the retained private manifest with its original digest and matching 17-file map;
no private manifest or runtime identifiers are copied into tests or this document.
Four skips are existing DynamoDB-CAS-only cases in SQLite parametrizations;
producer rollback is exercised on both SQLite and DynamoDB and its concurrent
publisher CAS test passed. Two dependency deprecation warnings; no installs,
full-suite run, cloud requests, production records or inference.

Repository has no `origin/main`; remote default is `feat/local-first`. The supplied
worktree base was `ca333d0` (already on `origin/feat/studio-opus-deploy-now`). The only
added dependency is already-published owner fix `1fdbe52` on
`origin/fix/diagnostic-live-compat`. Producer publication must introduce only its
own commit beyond these existing remote commits; no force push or PR.
