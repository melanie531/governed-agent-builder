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

## Observed integration receipt (2026-09-12, bounded worker)

Source implementation **845051955edb0c78219f425eaae07bea7f960df0** pushed to
private `melanie531/governed-agent-builder`, `feat/live-capabilities`, before
CloudFormation prepare. Started clean at requested 1bd9358; no concurrent
project writer observed. `origin/main` absent; approved baseline and actual
remote feature tip matched exactly. No PR/merge/force push.

- Target checks: current STS matched existing Studio CFN/CloudFront/Cognito
  internally before writes and after probe. No identifiers persisted.
- 48 targeted tests PASS. Full suite once: **735 passed**, 3 baseline warnings,
  49.69 seconds. `/tmp/gab-model-gate-full.log`. No frontend/UI changes.
- Actual CreateChangeSet: `model-gate-1789149160`, request
  `c58dc171-9b67-4a7d-8372-dca54ed9916d`. **NOT EXECUTED**.
- Proposed Add: ModelGate, ModelGateRole, ModelGateLogs. Proposed non-replacing
  Modify: ModelRole.Policies, ModelGateway.InterceptorConfigurations/RoleArn.
- Additional dependencies caused guard **UNREVIEWED_DEPENDENCY**:
  FoundationRole.Policies via ModelGateway.GatewayArn; ToolPolicy.Definition via
  FoundationRole.Arn. Both Dynamic / ResourceAttribute / Never recreation.
  Entire FoundationRole and ToolPolicy resources in original and ChangeSet
  templates compared equal. This does NOT authorize executing them; no guard
  exception was introduced. Resolved IAM/Cedar equality needs review/readback.
- Deployed interceptor count remains **0**. No Lambda/log/role has been created by
  this slice. Stack remains its prior stable state, not a deployment success.
- Live target metadata confirms READY `claude`, Mantle endpoint, operation
  `/v1/messages` -> `/anthropic/v1/messages`, model
  `anthropic.claude-haiku-4-5`. GetGatewayTarget initially rejected the CFN composite
  physical ID; retry correctly split its `gateway|target` representation. This
  was one metadata validation failure, NOT an inference or provider retry.
- ONE actual operator-signed unknown-model HTTP probe on the dedicated inference
  route returned **404**, safe diagnostic markers model/target/not found;
  request `f2c3afe0-8cdc-4bc5-a053-b8aa24191315`. This is existing Gateway routing
  behavior with no interceptor attached, NOT interceptor denial or harness proof.
  No operator access-denied result observed; no authenticated-user inference
  authorization is inferred. Raw body and prompts were not saved.
- Provider request non-invocation remains **UNPROVEN**. No CloudTrail provider data
  reads authorized/attempted, no synthetic HTTP target created, no Lambda service
  fault injections performed. ENFORCE coexistence at inference execution remains
  unproved; the unknown-model 404 cannot establish a Cedar inference schema.

Counts: one ChangeSet create, zero execute; one Gateway HTTP attempt; zero
positive inference attempts or provider grants; zero Runtime/tool/Browser/eval
calls. Metadata read count not instrumented. Actual cost unknown, no inference
pricing or remaining-budget waiver. CreateInference Deny unchanged.

**Next exact review:** approve only the two byte-identical, non-replacing CFN
resource-attribute dependencies above, with live resolved-role/Cedar equality
checks; otherwise retain this unexecuted ChangeSet. Then deploy the fixed-source
candidate and perform controlled HTTP/inference fault-path probes while provider
Deny remains. Only AFTER successful fault evidence and independent peer review
prepare an exact model-conditioned inference Allow diff. No Allow diff is offered
now because its prerequisite has not been met. This is an implemented/published
candidate plus a real scope blocker, NOT a whole-journey success.

## Authorized dependency continuation review

The user's existing project integration authority explicitly covers reviewing
and executing the existing ChangeSet after proving these two dependencies. This
is not a new permission grant or an approval to weaken the quarantine.
Started at clean 48b372c (a3812cd plus documentation-only receipt handoff).
Live SDK review now compares every existing role's full inline-policy set and
trust against exact intrinsic resolution from actual IAM/Gateway/PolicyEngine
identifiers. No attached managed policies or unreviewed boundaries admitted.
Active ToolPolicy Cedar definition, enforcement mode, actual principal ARN and
Gateway resource resolve exactly; both actual Gateway ENFORCE bindings match.
The desired template equals the original reviewed overlay, not a regeneration.
FoundationRole and ToolPolicy entire definitions remain identical.

The guard permits ONLY the enumerated Dynamic/ResourceAttribute/Never details:
FoundationRole.Policies <- ModelGateway.GatewayArn and
ToolPolicy.Definition <- FoundationRole.Arn; exact type, Modify and no replacement
are mandatory. Missing live semantic evidence, changed definitions and all
other dependency details fail closed. Execution reruns the live comparison and
compares receipt digests immediately before writing. Readback repeats IAM/Cedar
checks, all preexisting physical IDs, exact interceptor binding and downloaded
Lambda ZIP digest plus index.py bytes against the reviewed template.

The bounded probe was corrected to remove the old quarantined-valid case:
unknown model, malformed JSON, stream=true, excessive max_tokens only. Four
maximum attempts, each persisted before transport; no automatic retry. Safe
request-ID log correlation is reported, never inferred from 403 or token usage.
Timeout/throttle/malformed-service experiments remain pending and unauthorized
for mutation in this slice. No UI/runtime fixtures or existing Studio resources
are edited. Existing Well-Architected caveats above remain in force.

Validation: 64 focused tests passed; full suite run once, 751 passed with
3 baseline deprecation warnings. Final preparation-receipt cleanup rechecked
with the 64 focused tests. Cloud execution still pending at this source commit.

## Deployed candidate receipt, continuation

Fixed source `d8e85daad337edbccc1c2afac8c20b44c23b94cc` pushed before execution. Existing ChangeSet
`model-gate-1789149160` executed exactly once, request
`0a7e30c1-1abd-4d20-bd0b-18fd48b0fa8f`. Stack reached UPDATE_COMPLETE with exact desired
template readback. Subsequent DescribeChangeSet returned ChangeSetNotFound,
so no post-execution ChangeSet status is claimed and no retry was attempted.

Only ModelGate, ModelGateRole and ModelGateLogs added. All preexisting physical
IDs and stack outputs unchanged. Fully resolved IAM policies/trust match actual
roles, with only the reviewed exact ModelRole Lambda invoke addition.
FoundationRole IAM and ToolPolicy Cedar digests unchanged, including actual
principals/resources. Both ENFORCE attachments and CreateInference Deny remain.
Studio account/CFN/CloudFront privately verified before execution and after probes.
No Studio/Cognito/users/data/SSM, trust, BPA, runtime or UI-fixture mutations.

Lambda ZIP SHA256 `f154b8b37dd88a266a1dcb1837fd7bfcafc56dba2a7c3c1334a6701827ed8044` matches CodeSha256.
Sole index.py bytes match template/source SHA256
`84718a6d19b39659a420db0811990a9d0f2ce0ea6bf79ed0ccec00a091632669`.
Actual interceptor config exactly REQUEST, same Lambda ARN, no request headers.

| Actual inference-route negative | HTTP/code | Correlated request ID |
|---|---|---|
| unknown_model | 403 / MODEL_GATE_DENIED | `c06a3251-5087-4266-ad10-3f2da776c638` |
| bad_json | 403 / MODEL_GATE_DENIED | `41b666bd-b613-4704-9d6b-61c1638dd453` |
| stream_true | 403 / MODEL_GATE_DENIED | `179fc84d-43ac-4059-ae93-325e62f84c51` |
| excessive_max_tokens | 403 / MODEL_GATE_DENIED | `f3909605-b881-4ab0-a6ba-2b5f9cd72b13` |

All four request IDs matched safe MODEL_GATE_DENIED interceptor logs. The first
read had two; one delayed metadata-only read found all four. A log continuation
token remained, so unrelated log completeness is not claimed. No pre-interceptor
ENFORCE block observed for these four requests. Documented short-circuit contract
plus correlated decisions supports interceptor rejection; independent provider/
target non-invocation telemetry remains UNPROVEN, not inferred from 403 or tokens.

This slice: one execute, four negative Gateway requests, zero positive requests,
zero inference grants, zero fault-injection mutations, zero unknown-outcome
retries. Cumulative five Gateway requests includes prior undeployed 404 baseline.
Actual billed cost and remaining budget unknown.

Remaining exact evidence: Lambda timeout, throttling and malformed-service
response fail-closed experiments through actual inference route; independent
target/provider non-invocation evidence; applicable approval/peer review before
any inference Allow diff. None prepared. Candidate enforcement progress only,
NOT whole-platform acceptance. Private artifacts/model-gate/proof.json retains
write states and detailed sanitized evidence.
