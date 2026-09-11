# Non-streaming Model Gateway candidate

## Boundary

This candidate implements actual HTTP REQUEST enforcement, not a Cedar schema.
Only `POST /inference/v1/messages`, exact target-qualified
`claude/anthropic.claude-haiku-4-5`, text messages, optional text system, <=1744
raw/canonical bytes, and integer max_tokens 1..256 are admitted. The byte limit
plus existing foundation's 256-byte allowance is <=2000; this is a conservative
budget convention, NOT measured tokenizer usage. stream must be false or absent.
Tools, image/content blocks, arbitrary control fields and other operations are
intentionally denied in this first no-tool inference slice. Existing ModelClient
text-only calls are compatible; its tool-enabled calls deliberately are not.

Duplicate JSON keys, NaN/Infinity, malformed UTF-8/base64/JSON, noncanonical base64,
oversized bodies, incorrect event versions/protocols and routes fail to a constant
base64 JSON error in transformedGatewayResponse HTTP 403. No header is treated as
identity. AWS client context is Gateway metadata, not a human principal.

AWS_IAM admits workloads; platform admission separately validates current user,
run, grants, version and reservation. This Lambda replaces neither. Existing
FoundationRole exact Gateway invokes/direct-provider and direct-Lambda Denies
remain unchanged. No direct user invoke Allow is introduced; the existing
operator SDK transport test is not app-user or workload acceptance.

## Official service contract checked 2026-09-12 Sydney

1. https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-interceptors-types.html
   HTTP/inference payload uses http.gatewayRequest.body base64. REQUEST
   transformedGatewayResponse short-circuits without target; HTTP interceptors
   are not supported in streaming mode. Client context contains Gateway ARN,
   account, request ID and optional IP, NOT authenticated user identity.
2. https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-interceptors-examples.html
   Inference REQUEST interception occurs before routing; model target-qualified
   form is targetName/modelId. The sample's parse-error passthrough is expressly
   NOT copied. Current deployed target metadata, not an alias guessed from this
   sample, must establish the Haiku route.

These sources do NOT prove service-level Lambda timeout/error/throttle failures
fail closed. Source exception handling is not evidence about service failures.
Native inference Cedar action/schema remains unverified. Model and Tool Gateway
Policy ENFORCE stay attached; no LOG_ONLY/detach fallback is implemented.

## Infrastructure and evidence

`infra/model_gate.py` overlays the LIVE owned foundation template. It adds one
Lambda, a role with only exact log write permissions, and seven-day logs, then
adds exact Lambda invoke to ModelRole and REQUEST/no-headers to ModelGateway.
All other resources, role trust, outputs and inference Deny remain unchanged.
The ChangeSet guard refuses replacement, removal, unknown dependencies or any
other resource changes. `scripts/model_gate_probe.py` persists write outcomes
and probe attempt counts before dispatch, with no retries of uncertain work.
Only sanitized IDs/codes/digests and the Lambda's safe decision logs are saved.
No prompts, account IDs, raw service errors or credentials are persisted.

Implementation/source tests do not demonstrate live provider non-invocation.
A 403 alone cannot prove no provider request. A valid quarantined request may
reach provider IAM and be denied; no successful model inference is authorized.
A controlled service-failure experiment and correlated target/provider evidence
are still required BEFORE peer review of any Deny removal. The first driver does
not mutate Lambda to inject faults or silently attach a synthetic HTTP target.
Such a scoped fault experiment must retain provider Deny and independently check
that it exercised the HTTP inference path, not an earlier Policy rejection.
CloudTrail provider data access is not presumed authorized or enabled.

No positive inference is permitted by this source. A later reviewed single-call
allow diff needs exact fixed SHA, successful gate/fault evidence, applicable
model metadata and published pricing, remaining US$5 overnight envelope, US$1
reservation, <=2000 input / <=256 output, and no automatic provider retry.
Haiku input/output rates are not newly verified here; no guessed price gate.

## Well-Architected review

- Security: exact request allowlist and IAM separation; service failures and
  direct-user prohibition still need live authorization evidence, Deny retained.
- Reliability: bounded parsing, constant errors, irreversible attempt records;
  Lambda-service timeout/throttle behavior remains unproved.
- Performance: 128 MiB/3-second Lambda, bounded input; live latency unmeasured.
- Cost: no inference grant; existing $5 envelope, pricing/remaining spend unknown.
- Operations: fixed-source ChangeSet/readback and sanitized proof; no full journey claim.
- Sustainability: one on-demand Lambda and expiring logs, no always-on resources.

## Continuation

Private receipt: `artifacts/model-gate/proof.json`. Read it before attempting any
operation; do not rerun prepare/negative blindly. Deployment/negative results and
source SHA are appended after observation. Runtime/Browser/evaluation/UI work is
outside this slice. No nested worker, new credentials, SSM, user records or UI
changes. Memory recall timed out; this report uses direct source and service data.
