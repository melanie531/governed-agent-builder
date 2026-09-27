# Live ALPR integration

Entry: `python -m runtime.mcp_specialist.alpr_live`. Only the three curated ALPR
specialists, version 1, are served. Business arguments remain exactly
`{"question":"..."}`. Real connector results require Snowflake query IDs and
label SYNTHETIC data and policy DEMO ASSUMPTIONS. There is no snapshot fallback.

## Implemented admission

`runtime/journey/main.py` passes a backend-issued `alpr_run_reference` from the
invocation envelope to `RunExchange`. It never enters business arguments, model
messages, traces, or returned evidence. `Journey.invocation()` creates the reference
inside the existing serializable repository before invoking A. It binds the exact
stored input/history/request ID, run owner/workspace, Runtime/session/version,
manifest, foundation and immutable deployment record. It expires after at most
180 seconds. A resolves it once using SigV4; subsequent calls must use that exact
verified IAM role session. A different session, replay, wrong input, wrong
manifest or current grant denial fails before model/discovery/output.

`backend.journey_alpr_run.dispatch()` is wired to the dedicated API Gateway IAM
handler. It implements resolve, authorization, issuance, completion, specialist
redemption and authenticated platform listing. Every operation rechecks current
Journey authority; each business call and each view still checks current grants.
The existing 60-second single-use call references and six-call budget remain.

The workload verifier calls real AWS control-plane and IAM APIs. It requires:

- A distinct execution role for A and B. Trust must name the exact Runtime ARN
  and source account, with no alternate trust statement.
- The latest Runtime and DEFAULT endpoint must match the registered numeric
  version and entire captured configuration. No older version may have used the
  deployment role. Use a fresh role for a final version after any bootstrap.
- Immutable image digest or versioned code artifact. A registration additionally
  matches the exact saved Foundation ZIP, entry point, and manifest.
- Exact invocation policies on **both Runtime and DEFAULT endpoint**: allow only
  the backend Worker role for A, the Journey Gateway role for B; explicit deny
  overrides other same-account invocation grants. Runtime command/shell access
  is denied to everyone by these policies.
- No managed policies on workload roles, no IAM/admin/state-store permissions,
  explicit allowed actions and bounded resources. A cannot read SSM/KMS; B cannot
  invoke models/Gateway or access S3. B's SSM reads are only the existing account,
  user and private-key parameter ARNs. The exact inline policy documents are
  included in the immutable record and compared on every workload check.

Registration writes a content-addressed deployment record and exclusive role
indexes. No business route can register one. `JourneyCloud.create()` uses the
registered immutable A deployment for ALPR; it never falls back to the shared
Journey role. A deploy request without a registered deployment fails closed.

## Authenticated Gateway tools/list

With no call header, the real Runtime B adapter signs a `platform-list` request
using B's credentials. The backend verifies B's exclusive role/version and the
Gateway-only Runtime/endpoint invocation policies before returning only the three
descriptors. It returns no scope, reference, or invocation authority. This relies
on the managed Runtime IAM ingress, not a caller identity header. A missing HTTP
request, invalid header, or duplicate header does not enter this path.

With a valid call reference, listing is restricted to that call's tool, preserving
MCP 1.30's listing during call preparation. An invocation without a call reference
still denies before Snowflake. Listing can be withdrawn by the protected
`journey-alpr-listing:revoked:<specialist deployment digest>` record; an individual
A deployment by `journey-alpr-deployment:revoked:<record digest>`.

## Host-only build and release

No dependency installation, credential access, deployment or SQL provisioning was
performed by the sandbox worker. The live lock is still a host build dependency:

```sh
./scripts/build_alpr_live.sh
```

This generates the missing live hash lock with `uv pip compile`, builds ARM64 with
`--require-hashes`, stamps the source commit, and import-tests the image. Record
and use the immutable ECR digest. The Dockerfile does not use the demo lock. The
base image tag is not digest-pinned; pin the observed base digest for reproducible
release builds. Package/upload the new backend/Foundation source using the existing
release packaging flow, then save the agent against that Foundation artifact.

Create the exact A/B runtimes and fresh dedicated roles on the host. This release
script deliberately operates on explicit **existing** runtime ARNs. It does not
create or update Runtime versions, roles, users, keys, databases, or views. A needs
its versioned `JOURNEY_MANIFEST` and `JOURNEY_ALPR_ENDPOINT`. Its entry is the existing
Journey Foundation ZIP. B needs the live image and the configuration below. Any
bootstrap version must use a different role from the final deployment version.

Use the existing target-bound state and profile `platform-dev-takeover`:

```sh
.venv/bin/python -m scripts.journey_alpr_release prepare \
  --expected-account ACCOUNT --profile platform-dev-takeover --region us-west-2 \
  --state EXISTING_TARGET_STATE --runtime-a EXACT_A_ARN --runtime-b EXACT_B_ARN \
  --release-key releases/SHA256/lambda.zip --release-version S3_VERSION \
  --output /tmp/alpr-release-review
```

`prepare` performs AWS reads, then emits `app-template.json`, `policies.json`,
`deployment.json`, and `gateway-target.json`. Policy documents bind the actual
runtime/endpoint/role ARNs. Parent must install the emitted trust, ingress and
route-invoke policies plus the narrow model/artifact/evidence/log permissions A
needs and image/log/SSM permissions B needs. The verifier rejects the older shared
role policies. Use exact resources, including A's evidence prefix
`journey/evidence/<definition digest>/*`. Add only B invocation to the existing
Gateway role. Preserve the risk-specialist target and existing policies.

Run the same command with `apply` to submit an additive app stack update. It adds
the IAM route, source-scoped Lambda permission, and an admission-only Lambda role;
updates Business/Worker/new exchange to the explicit versioned ZIP; and preserves
other resource definitions and previously registered ALPR targets. No wait loop
or catalog publication occurs. Wait for the host stack update to finish, reconcile
the existing target state outputs if needed, then use `register` with
`--channel-evidence /path/to/actual-forwarding-smoke.json`. Registration verifies
AWS state and the protected manifest before writing the immutable record. Use the
rendered Gateway target to register/synchronize only the distinct ALPR target.
Publish only its observed descriptors using existing catalog administration, then
run Deploy and Invoke through Studio. Registration does not auto-grant tools or
mark catalog records live-ready.

The route is `POST /internal/journey/alpr`, API Gateway payload v2, `$default`,
AWS_IAM `requestContext.authorizer.iam.userArn`. Source ARN-scoped Lambda permission
and the workload roles' lack of direct Lambda invocation are part of the boundary.
The generated exchange role has no model, Snowflake or SSM permissions.

## Explicit provider dependency — host proof still required

Header: `x-gab-alpr-capability`, a bearer reference, not workload identity. Runtime A
sets it before SigV4 signing. AWS documents these configuration surfaces:

- [Gateway headers](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-headers.html):
  target `metadataConfiguration.allowedRequestHeaders`.
- [Runtime header allowlist](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-header-allowlist.html):
  `requestHeaderConfiguration.requestHeaderAllowlist`.
- [Runtime security](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-security-best-practices.html):
  exact ARN trust conditions and explicit-deny Runtime/endpoint policies.

These fields exist in installed botocore 1.43.92; schema validation and the offline
Gateway simulation are **not forwarding proof**. The parent must observe the exact
lowercase header through actual A -> Journey Gateway IAM target -> B, including
missing, duplicate and spoofed-reference tests. Do not fabricate a forwarding
channel, infer forwarding from ordinary HTTP, or treat a digest as proof. A
separate controlled forwarding probe may be needed before final image/config
registration; do not publish a probe as an authorized specialist deployment.

B's protected `ALPR_LIVE_CONFIG` has exactly:

```json
{
  "endpoint": "https://API.execute-api.us-west-2.amazonaws.com/internal/journey/alpr",
  "runtime_arn": "EXACT_B_ARN",
  "runtime_version": "NUMERIC_VERSION",
  "deployment_digest": "SHA256_OF_SPECIALIST_IDENTITY_AND_ARTIFACT",
  "channel": "gateway-target-header-runtime-allowlist-v1",
  "channel_evidence_digest": "SHA256_OF_ACTUAL_HOST_FORWARDING_EVIDENCE"
}
```

`scripts.journey_alpr_release prepare` computes `deployment.specialist` from the
observed B ARN/version/role/artifact; this digest excludes environment to avoid a
self-referential hash. B also requires `ALPR_VIEW_SOURCE=live`,
`AWS_REGION=us-west-2`, `ALPR_SNOWFLAKE_SSM_PREFIX=/governed-agent-builder/alpr`, and
`requestHeaderConfiguration.requestHeaderAllowlist=["x-gab-alpr-capability"]`.
Environment/file credentials and demo caller profiles remain rejected.

## Limits and acceptance

Cloud configuration/IAM administration and the backend registration repository
are trusted control-plane boundaries. AWS IAM propagation and races with an
operator changing policies are not cryptographic workload attestation. Runtime
role sessions are pinned for a run; credential-session changes require a new run.
Strict policy equality intentionally rejects equivalent but unreviewed variants.

No live forwarding, image build, IAM propagation, endpoint policy, Snowflake
query, or cold/warm latency success has been claimed. Control-plane/IAM reads
add latency: B's exchange requests remain bounded at three seconds and its whole
business call at twenty seconds. Measure this on the host; timeout fails closed.
Do not claim Python can forcibly cancel every blocked native connector/DNS call.

Run the eight ALPR cases through Studio -> A -> Gateway -> B -> live approved
Snowflake views. Correlate query IDs with QUERY_HISTORY and A's persisted spans;
exercise grant/scope/workspace/version revocation, missing config/header, replay,
wrong role/session, and expiry. The code paths are integrated and offline-tested;
live publication remains gated on these real provider/deployment checks.
