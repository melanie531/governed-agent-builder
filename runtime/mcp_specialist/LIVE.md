# Live ALPR integration slice

Entry: `python -m runtime.mcp_specialist.alpr_live`.

This entry serves only the three platform-curated ALPR specialist tools, version
`1`. Arguments remain exactly `{"question": "..."}`. It runs the existing
specialist manifests and real Snowflake connector, requires query IDs in every
successful inner result, and reports SYNTHETIC data and policy DEMO ASSUMPTIONS.
No business Lambda queries Snowflake on this path.

## Deliberately blocked production boundaries

This checkout is **not ready for live publication**. It implements the specialist,
redemption service, and admission ports, but does not pretend to authenticate a
Journey caller using today's shared Runtime A role.

1. `GatewayMCP(alpr_admission=...)` needs a host-reviewed signed workload exchange
   adapter. `resolve()` must return the existing `Binding`, authenticated against
   the backend's single run and exact Runtime A version. `authorize(binding,
   operation, resource)` must recheck the backend for `start`, `tool`, and `finish`.
   `issue(binding, qualified_tool, arguments)` must invoke the server-owned
   `backend.journey_alpr.issue()` after validating that same signed caller.
   `aws_dependencies()` intentionally provides no such adapter: native ALPR
   invocation fails **before model/discovery/output** with
   `ALPR_AUTHENTICATED_RUN_EXCHANGE_NOT_CONNECTED`.
2. The server-side issuer's `verify_workload(db, binding, specialist)` must return
   the identical Binding only after verifying the exact A/B runtime versions,
   immutable manifest/artifact, dedicated execution roles and invocation policies.
   Its default raises `ALPR_EXACT_RUNTIME_WORKLOAD_PROOF_REQUIRED`. Today's shared
   Journey Runtime A role cannot supply that proof. Do not replace it with a
   no-op verifier, configuration boolean, payload identity or static full scope.
3. Gateway synchronization calls `tools/list` without a user capability. The
   production specialist adapter requires a valid call reference for listing and
   permits only that call's tool. A **separately authenticated platform listing
   adapter** is still needed for synchronization. It must never mint invocation
   authority. There is no anonymous or deployment-wide full/no-data listing
   fallback. MCP 1.30 also consults its listing handler while preparing tool calls,
   so this is part of the admission contract, not just UI discovery.

These are code/architecture integration blockers, not tasks that a dependency lock
or a successful image build can resolve.

## Implemented backend contract

`backend.journey_alpr.issue()` stores an unpredictable, 60-second, single-use
capability in the existing serializable settings repository. It binds the exact
run owner/workspace, Runtime A/session/manifest/foundation, specialist version,
Runtime B workload/version/deployment digest, question digest, and existing
admin-written caller scope. Maximum six issued calls per run session.

`exchange()` accepts `inspect`, `redeem`, `authorize`, `view`, and `finish`.
Each operation calls current `Journey.authority()` and reads current caller
scopes. Missing grants, scope narrowing, workspace/version changes, wrong workload,
expired references, substituted arguments, replayed calls/views and incomplete
completion deny. Each view claim is persisted before dispatch. Failed calls are
not replayed; no token or caller identity is inserted into a business prompt.

The dedicated handler is `backend.serverless.journey_alpr_exchange_handler`.
It is **unregistered and disabled by default**. Its only allowed route is
`POST /internal/journey/alpr`, API Gateway payload v2, `$default` stage, AWS_IAM
authorizer `requestContext.authorizer.iam.userArn`. Configuration requires
`JOURNEY_ALPR_EXCHANGE_ENABLED=1`, `JOURNEY_ALPR_API_ID`, `STATE_TABLE`, and the
existing HostedAuth configuration used by `Journey.authority()`.

A reviewed infrastructure change must add this exact route, source-scoped Lambda
invocation permission, current-authority table reads and settings writes, and
`execute-api:Invoke` for Runtime B on this route alone. Direct Lambda invocation
must not let Runtime/browser callers forge API Gateway context. This handler needs
no model, Snowflake or SSM credential permissions. Existing IAM/model policies are
unchanged in this commit.

## Documented transport channel requiring host smoke

Header: `x-gab-alpr-capability`. Its value is a backend-issued bearer reference,
**not an authenticated identity**. Runtime A includes it before SigV4 signing.
The reference is not in arguments, trace spans or returned business evidence.

AWS documents both configuration surfaces:

- [Gateway header propagation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-headers.html):
  target `metadataConfiguration.allowedRequestHeaders`.
- [Runtime header allowlist](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-header-allowlist.html):
  Runtime `requestHeaderConfiguration.requestHeaderAllowlist`.

Both fields exist in the installed botocore 1.43.92 model. Offline SDK shape
validation is **not forwarding proof**. The exact lowercase header must pass
through the actual Journey Gateway IAM target into Runtime B, including duplicate,
missing and spoofed-reference tests. Do not infer propagation from ordinary HTTP.

`scripts.journey_platform.alpr_target_configuration(binding, runtime_arn)` renders
a distinct `alpr-investigation-specialists` target using `GATEWAY_IAM_ROLE`,
`iamCredentialProvider` (`bedrock-agentcore`, `us-west-2`), and the header allowlist.
It performs no writes or publication. Runtime B separately needs:

```json
{"requestHeaderConfiguration":{"requestHeaderAllowlist":["x-gab-alpr-capability"]}}
```

`ALPR_LIVE_CONFIG` is a protected deployment JSON with exactly:

```json
{
  "endpoint": "https://<api-id>.execute-api.us-west-2.amazonaws.com/internal/journey/alpr",
  "runtime_arn": "<exact Runtime B ARN>",
  "runtime_version": "<numeric immutable version>",
  "deployment_digest": "<reviewed deployment digest, 64 lowercase hex>",
  "channel": "gateway-target-header-runtime-allowlist-v1",
  "channel_evidence_digest": "<actual host smoke evidence digest, 64 lowercase hex>"
}
```

The evidence digest is a pinned release prerequisite, not runtime identity proof
or a replacement for backend redemption. Do not fill it with an arbitrary value.

## Build and host verification

No lock generation or installation was attempted by this worker. On the networked
host, after the parent's active MCP installation finishes:

```sh
uv pip compile runtime/mcp_specialist/requirements-live.in --generate-hashes \
  --python-version 3.13 --python-platform aarch64-manylinux_2_28 \
  -o runtime/mcp_specialist/requirements-live.lock
docker buildx build --platform linux/arm64 \
  -f runtime/mcp_specialist/Dockerfile.live --load -t gab-alpr-live:review .
docker run --rm --entrypoint python gab-alpr-live:review \
  -c 'import boto3, snowflake.connector, mcp; import runtime.mcp_specialist.alpr_live'
.venv/bin/python -m pytest -q tests/test_alpr_live_runtime.py \
  tests/test_journey_alpr_admission.py tests/test_alpr_transport_admission.py \
  tests/test_alpr_live_exchange_protocol.py tests/test_alpr_query_isolation.py \
  tests/test_alpr_live_unified.py tests/test_alpr_specialists.py \
  tests/test_mcp_server_entrypoint.py
```

The live Dockerfile fails to build until its separate hash lock exists. It cannot
silently install the old demo lock. Record source SHA and immutable ARM64 image
digest. Never run the older demo deploy script as the ALPR release.

Runtime B requires `ALPR_VIEW_SOURCE=live`, `AWS_REGION=us-west-2`, and
`ALPR_SNOWFLAKE_SSM_PREFIX=/governed-agent-builder/alpr`. A demo scope profile and
env/file credential overrides are rejected. Grant B `ssm:GetParameter` only for
the existing `/account`, `/user`, `/private-key` parameter ARNs, with narrowly
scoped KMS decrypt if their existing key requires it. Do not recreate data/users/
keys. Prove the readonly role, disabled secondary roles and approved-view grants.

The call shares a 20-second deadline across backend exchange and connector work.
SSM retries are disabled on this entry; login/network/statement budgets are
bounded by remaining time. Snowflake synchronous cancellation, DNS, cold imports,
fetch time, and total Gateway latency still require cold/warm host tests. An
expired call cannot start another view or publish success, but this is not a claim
that Python can forcibly kill a blocked native connector thread.

Finally publish only observed Gateway descriptors and approved caller scopes into
the Journey catalog, save/deploy a new Runtime A manifest, and run all eight cases
through Studio -> Runtime A -> Journey Gateway -> Runtime B -> real Snowflake.
Correlate real query IDs with QUERY_HISTORY and the persisted Runtime A spans;
exercise revocation, wrong workspace/version, missing config and absent header.
No catalog entries are marked live-ready by this slice.
