# Run evidence exporter: source contract, not cloud validation

Status: implemented source integration; deployed=false. No AWS production writes,
model/browser calls, credential reads, role changes, or live evaluation were made.

## Actual caller chain

`configured_jobs(worker=True)` injects `RunEvidenceExporter` whenever the existing
protected evaluation collector is enabled. `FoundationJobs.step(EVALUATING)` claims
a 240-second worker lease, calls exporter, then collector, then reader. Logs waits
release only that lease and retain EVALUATING. The existing SQS worker schedules a
10-second continuation; there is no HTTP sleep. The worker factory now enables
worker dependencies for LIVE as well as PRODUCER. No new approval module exists.

The actual `Engine.run` records ended OTel spans with an in-memory SpanProcessor.
It returns those spans and private content in its Invoke response. The backend
freezes that authenticated, version-pinned response with a digest and the actual
Invoke SDK request ID. This is not a user-uploadable descriptor or Runtime S3 URL.
The existing IAM exchange independently records model/tool claims and settlement.
The exporter requires exact span counts against that ledger, actual request IDs,
root/child timestamps, one session/trace, manifest/foundation binding, finished
settlement, and provider token values matching model spans. A complete boolean or
`otel_exported` is never authority. An unexpected/unseen parent is rejected, not
manufactured. Gateway internal spans are not invented or claimed covered: coverage
is the owned runtime's emitted run/model/tool spans only.

`ModelClient.generate` captures actual inference messages; `ToolClient.call`
captures validated MCP tool arguments/results on the actual tool span. `Telemetry`
keeps these private attributes out of its operational OTLP export. Scope and
operation/session metadata now use documented generic GenAI conventions.

## Data classification and readback

- CloudWatch: actual IDs, timestamps, parent links, operation, session identifier,
  manifest/foundation digests, request IDs, provider/route and usage metadata only.
- Private Invoke response and server run row: immutable execution record, output,
  model messages, system input and validated tool results. Never API-projected.
- Versioned private S3: bounded 64 KiB, binding-scoped content-addressed objects,
  AES256, Block Public Access, conditional create, version-pinned readback and SHA.
  Existing object conflicts read and verify the version; they do not overwrite it.
- Evaluate: only actually observed spans, with private content joined to those
  exact span IDs using AWS-documented generic attributes. No invented native fields.

The report is the exact textual model output. Sources contain the exact authorized
stored input plus actual tool-result text. Input source IDs are content-addressed;
tool source IDs use the actual span. Citations are not invented from prose. Current
runtime emits no structured citation mapping, so citations are empty; bracketed
references lacking mappings fail citation validation. No source URL is fetched.

Queries use real Logs `StartQuery` / `GetQueryResults`, an execution-time bounded
window, trace-ID filter, fixed emitted log group, and a 101-row sentinel for the
100-span cap. Responses must match actual recorded span IDs, timestamps, parents,
metadata and SDK request IDs. Running/scheduled/missing spans wait up to 12 polls
and 120 seconds, bounded also by run authority. Truncated/paginated/mixed/duplicate
results fail closed. A lost StartQuery response is uncertain, never blindly retried.

A completed export writes precisely `collection_input` (pin, source IDs/digest,
span IDs, query IDs, format) and `collection_grant` (immutable approval digest,
binding, input digest, single M0 TRACE case). CAS checks the current worker lease,
run authority, response and call-ledger fence. Approval bytes are never modified.
Collectors/readers use the new concrete format branch; old fixture-format tests
remain offline-only. Paid Evaluate retains its committed no-replay claim.

Missing evaluator configuration still allows the owner to read the verified
report via `RUN_EXPORT_ONLY`, with no evaluations and semantic quality INCOMPLETE.
Unknown rates remain NULL; available token usage is provider-derived, not estimated.
All-service reservation stays held. No new pricing assumptions or paid retries.

## Documented format and fixture provenance

Read 2026-09-12:
- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/supported-frameworks-generic.html
  specifies generic scope prefixes, identifying operations, task input/output,
  inference messages, tool name/arguments/results and system instructions.
- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/supported-frameworks-strands.html
  provides the flattened CloudWatch JSON shape (hex IDs, integer nanoseconds,
  scope, object attributes). `tests/fixtures/cloudwatch-public-span.json` pins the
  first published split-telemetry invoke-agent sample, reformatted only. AWS calls
  it representative and incomplete; it is NOT accepted as a full M0 execution.
- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/supported-frameworks-telemetry.html
- https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-OTLPEndpoint.html

The installed SDK models Validate Evaluate as a document-valued Span, not a typed
proof of GenAI semantic validity. SDK acceptance alone is NOT live provider proof.

## Read-only preflight for later authorized parent execution

Not executed here. After the parent authorizes the real read-only role: select one
already completed authorized M0 job from the server ledger; verify its response
pin, session/runtime/version and expected owned spans; run ONE bounded StartQuery
against `/governed-agent-builder/foundation-m0` for its exact trace/time window,
then bounded GetQueryResults. Compare returned JSON with this normalizer and the
actual recorded spans. Report schema/destination mismatch or incomplete coverage
without S3 writes, Evaluate, re-invocation, log dumping, or credential output.

Real blockers to verify: runtime package rebuild/admission for changed source;
current gateway model/tool and exchange IAM restrictions; worker Logs query and
private S3 permissions; actual OTLP destination (unified header-selected group vs
`aws/spans` routing), Transaction Search and ingestion; native generic Evaluate
acceptance; authenticated code-evaluator entry if selected. This source slice does
not change those settings or claim they work.

## Well-Architected / security review

Security: job-bound reads, immutable authority and fail-closed CAS; real IAM pending.
Reliability: durable bounded query continuations, leased owner, no paid replay;
uncertain StartQuery/Evaluate requires operator reconciliation rather than retry.
Performance: one bounded query/poll per worker step, 100 spans/64 KiB.
Cost: fixed poll cap, existing reservation and call ledger, unknown prices NULL.
Operations: safe fixed failure codes, source tests; cloud preflight outstanding.
Sustainability: no extra model calls, bounded query work and content-addressed reuse.

Pinned public sample SHA-256: `16bbc32d4a5227c00661488138d13afb654bbf458efed4ea1ad2507f8a37e8b7`.

## Work report

Baseline: `8a00504317b27776d500ec8c20423e11cd12eeb5` on `feat/live-capabilities`.
Focused integration/regression run: **231 passed** across exporter, executor,
collector, code evaluator, evidence, foundation wiring and serverless tests.
Final private-content/input-cap hardening rerun: **59 passed** across exporter and
executor (includes eight new cases). Finalization reran all seven focused modules
against source commit `8446af86455bb8a832faec6845d9b63de0181703` after that hardening:
**239 passed, 2 deprecation warnings in 30.32 seconds**. Modules: exporter, executor,
collector, code evaluator, result evidence, foundation wiring and serverless.
Outbound socket connect/connect_ex were blocked for the entire pytest process;
AWS config/credentials file lookup pointed to `/dev/null`, metadata lookup disabled.
Only offline fixtures, SDK Stubber and Moto were used. No source edits followed.
Frontend unchanged, so no frontend build was run.

Private capture rejects recognizable credential-bearing keys, bearer credentials,
AWS access-key patterns and PEM private keys rather than redacting provenance into
misleading evidence. Inputs are type/byte checked before capture. Transport headers
are never captured. This is bounded defensive filtering, not a guarantee that
arbitrary free text contains no secret; source data handling/IAM remain necessary.

Original model-gate fault files were excluded and untouched. No PR, merge, force
push, deployment, credential read or live cloud test was performed. Normal source
publication is the only requested external write. Public sample is AWS's published
example, not any user's trace. Runtime/API raw-record privacy is maintained by the
existing explicit result projection; the new record is never part of that schema.
