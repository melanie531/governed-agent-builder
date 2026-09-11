# First custom foundation slice — 2026-09-12

Current (admission follow-through): **source published; additive backend IAM route UPDATE_COMPLETE; production admission NOT_READY**.
Existing owned foundation stack was previously **UPDATE_COMPLETE**; reusable executor implemented.
Live M0A / user journey **BLOCKED**, no inference attempted.
On `feat/live-capabilities`, initial clean HEAD `d0612a6`.
One implementer; no coding agents/workers. Current user implementation authority
supersedes historical plan-only notes within the requested scope.

## Latest verified result

- Latest source verification: **534 passed**, 3 baseline warnings, 30.55s
  (`/tmp/gab-admission-final.log`); unchanged implementation retained after self-review.
  Earlier 491/88 results below describe the preceding milestone.
- Owned stack `governed-agent-builder-foundation-m0`: **UPDATE_COMPLETE**;
  both Gateways/targets **READY**, AWS_IAM + Policy **ENFORCE**.
  Tool policy `synthetic_lookup-2sb3g0fbhn`: **ACTIVE**.
- Real Gateway-bound immutable configuration saved **before any Runtime exists**,
  source ZIP packaged locally at `artifacts/foundation-configured-before-deploy/`.
  Foundation source digest:
  `0733d059a2038037ced6ed56a89c28ae5831a58e773ba9d8b70290bfab4503e0`.
  Manifest digest:
  `ddfdbeb359ff94b5224d0017aa75a054a0cc1b91fff8cbeee46ee62dd2516a44`.
  Package digest:
  `9079cac6ccf2ed0236dff646418b2035949389b3becf304b788b13e76eced151`.
  These supersede earlier source/package digests below. Source-only: locked
  Linux ARM64 dependency assembly remains required before Runtime deployment.
- **0 inference attempts, 0 direct tool invokes, 0 Runtime invokes, 0 Browser
  sessions**. Provider tokens, live OTel export/readback and S3 content evidence
  **NOT_RUN**. Service-internal catalog discovery occurred during provisioning;
  its exact operation counts/billed cost are **unknown**. Definition provisioning
  estimate reservation **$0.001920** retained; no invoice/zero-cost claim.
  US$5 aggregate variable estimate cap remains in force, not a billing hard cap.
- Live blockers: exact **model inference Policy schema/coverage**, complete
  applicable rate card, approved existing **VPC**, authenticated per-Runtime
  backend redemption, scoped CloudWatch delivery/export/readback. Model role
  allows metadata ListModels only; **Mantle CreateInference explicitly denied**.
  UI remains fixture; compiler remains `execution_ready=False`.
- Implementation commit `aa91601e72158a7eabc79d9fe7d02e23ab728c1d` **pushed**
  to private `origin/feat/live-capabilities`. Approved baseline ownership verified.
  No other changes, shared resources, identities or legacy Gateways were modified.

Latest Well-Architected status: Security **gated** (owned IAM/ENFORCE provisioned;
live authority/model policy/Browser proof missing); Reliability **offline limits/
idempotency tested**, production lifecycle unproved; Performance **unmeasured
live**; Cost **reservation/one-attempt guards tested**, billed usage unknown;
Operations **real provisioning/readback**, live OTel NOT_RUN; Sustainability
**serverless bounded scope**, no always-on/provisioned commitments.

Milestone history below preserves the actual failures and corrections.

## Milestone 1: repository and API inspection

- Read implementation plan, minimal resource packet, topology decision and
  relevant repository compiler, Runtime adapter, source package and tests.
- Existing `backend/runtime_deployment.py` exact account/role/artifact/VPC
  guards preserved. Compiler remains `execution_ready=False`; fixture UI unchanged.
- Installed boto3/botocore **1.43.92**: inspected actual Gateway inference
  provider/connector, Policy/ENFORCE, Runtime code/container and Browser shapes.
- Baseline: **443 passed**, 3 existing deprecation warnings, 18.53 seconds.
- AWS gate attempted using only `agentic-platform-prod`, `us-west-2`.
  Exact sanitized error: `EndpointConnectionError: Could not connect to the endpoint URL: "https://sts.us-west-2.amazonaws.com/"`.
  STS/CloudFront/Studio stack/Cognito equality is **UNVERIFIED**.
- GitHub metadata: `error connecting to api.github.com`;
  `check your internet connection or https://githubstatus.com`.
  Remote privacy and freshness **UNVERIFIED**.
- Cloud writes/resources: **none**. Inference attempts/tool/Runtime/Browser calls:
  **0**. Provider tokens **NOT_RUN**. Live traces/evidence **NOT_RUN**.
  Variable reservation/spend **NOT_RUN**, actual billed cost **unknown**.
  Authorized estimate cap is US$5, not a billing hard cap.

Official inference docs advertise Policy governance but do not establish the
exact inference action/model Cedar schema. Model policy coverage **BLOCKED**;
tool policy coverage cannot substitute for this proof.

## Remaining acceptance

Executor/adapters/package, safe deployment/probe drivers, focused verification
and commit are in progress. Live acceptance needs verified target, exact supported
inference policy, selected route/rates, existing approved VPC path and actual
CloudWatch export/readback. Production authority is not connected.
Browser all-path egress, same foundation image with two configurations, native
code evaluator + Gateway judge, live UI catalogs, per-agent DDB/SQS lifecycle,
permissions/approvals/cost/observability/versions remain **NOT_RUN**.

Well-Architected: Security **blocked** on live authority/policy/network proof;
Reliability **unproved**; Performance **unmeasured**; Cost **no call admitted**;
Operations **local verification only**; Sustainability **bounded scope**, no
NAT/GPU/always-on/provisioned model commitments.

## Milestone 2: verified target and executable source

- The standard sandbox escalation mechanism permitted the bounded read-only
  retry. **Current** STS/Studio stacks/exact CloudFront/Cognito account and region
  match; no account identifiers persisted. Initial sandbox error above retained.
- GitHub managed auth verified remote **private**. Default remote branch is
  `feat/local-first`; task remains on `feat/live-capabilities`.
- Live Studio CloudFormation template: Business/Auth/Authorizer/Worker/Dispatcher
  Lambda functions have **no VPC configuration**. Bounded Runtime metadata list
  found **no project custom foundation Runtime**, no further page.
  Approved Runtime VPC binding is absent; PUBLIC fallback remains prohibited.
- Public live CloudFormation schemas expose Gateway/Target/PolicyEngine/Policy;
  inference target definitions present. Actual inference policy schema remains
  **BLOCKED**: official schema constraints describe MCP tool actions only.
- Added reusable `foundation_harness/` executor, controlled synthetic authority
  in `backend/foundation_authority.py` (no production route), focused tests.
  Test-first run failed at missing module as expected. First implementation run:
  18 passed, 1 test-double notification defect identified and corrected; recheck
  pending. No claim of live execution.
- `pyproject.toml`/`uv.lock` add optional `foundation-runtime`: pinned Runtime SDK,
  JSON Schema validator and manual OTel SDK/protobuf exporter. No auto-instrumentation.
- Cloud writes/resources still **none**; inference/tool/Runtime/Browser counts
  remain **0**. Real traces and provider usage **NOT_RUN**.

## Milestone 3: focused executor/delivery verification

- **75 passed**, 2 existing warnings, 0.57s: new executor and delivery tests plus
  existing backend Runtime/target tests. Generic model→tool→model execution uses
  only in-memory test responses; it is **OFFLINE_PASS**, not service evidence.
- Added deterministic `scripts/package_foundation.py`, source/lock digests and
  save-before-deploy content-addressed manifests. `backend/foundation_deployment.py`
  exposes Create-only per-agent deployment with stable idempotency, exact package
  manifest/source binding and existing approved VPC guard. Source ZIP still needs
  Linux ARM64 dependencies; no deployable artifact is claimed.
- Runtime entry: `runtime/custom_foundation/main.py`; production admission
  explicitly **BLOCKED** on supported authenticated backend redemption.
- CloudFormation template `infra/foundation.py`, create/inspect/execute/status
  driver `scripts/foundation_cloud.py`. It cannot update/adopt existing stacks.
  ChangeSet must exactly match template digest and only Add its declared resources.
  Separate named Gateway roles, Runtime role and network-free synthetic fixture.
  Model role explicitly denies Bedrock until inference policy is established.
- Model metadata: Haiku 4.5 agreement/entitlement/region AVAILABLE, AUTHORIZED.
  Official model card confirms Mantle `anthropic.claude-haiku-4-5`.
  Actual route/auth/Policy inference **NOT_RUN**.
- Official AgentCore pricing retrieved 2026-09-12 Sydney:
  Gateway $0.005/1,000 operations; Policy $0.000025/authorization;
  Runtime $0.0895/vCPU-hour and $0.00945/GB-hour.
  Source: https://aws.amazon.com/bedrock/agentcore/pricing/
  For definition provisioning, reserve **$0.001920** for an estimated upper
  allowance of 64 Gateway metadata and 64 Policy checks during schema validation.
  This is an estimate of service-internal work, not an enforceable service retry
  cap or measured invoice. Driver dispatches no tool/model/Runtime/Browser calls,
  uploads no content and exports no telemetry during provisioning.
  Empty retained S3/log resources have no retained content. No ECR is required for
  the direct-code path. Inference admission remains blocked; no token reservation.
- Exact applicable Haiku Mantle/CloudWatch/S3/Lambda rates are not all established
  by retrieved pricing pages. No live proof dispatch is permitted on guessed rates.
- `uv export` initially denied access to `/Users/peiyaoli/.cache/uv`:
  `Operation not permitted (os error 1)`. Re-ran using a permitted temporary cache;
  export succeeded. No safeguard bypass or credential-file access.

## Milestone 4: inspected project ChangeSet

- Created and inspected **only** stack
  `governed-agent-builder-foundation-m0` ChangeSet
  `foundation-509b16774fa4960215fb493df417617f`.
  Template SHA-256:
  `509b16774fa4960215fb493df417617f18618fe1577f4aa1a452df8e55757187`.
- Live ChangeSet **CREATE_COMPLETE / AVAILABLE**, exact digest matched,
  all **14 Add** changes inspected: 2 Gateways, 2 targets, 1 Policy engine,
  1 tool policy, 4 scoped roles, 1 synthetic Lambda, 1 private S3 bucket,
  1 TLS-only bucket policy, 1 seven-day log group.
- No Modify/Remove, Cognito/DDB/UI/network/legacy Gateway resources in the set.
  Execution pending at this milestone. Resources provisioned: **none yet**.
- Definition provisioning reservation remains **$0.001920**, actual cost
  **unknown**. Inference attempts **0**, token usage **NOT_RUN**.

## Milestone 5: execution submitted and offline proof recorded

- Executed the inspected ChangeSet after rechecking current Studio target.
  `DisableRollback=True` retains only newly owned resources for failure review.
- First status read: **CREATE_IN_PROGRESS**. Evidence bucket/TLS policy,
  synthetic Lambda/role, Model Gateway/role, Policy engine and seven-day
  Operations log group **CREATE_COMPLETE**. Remaining resource creation pending;
  no final success claimed.
- Expanded focused suite: **85 passed**, 2 existing warnings, 0.47s.
- `python -m scripts.foundation_probe --output-dir artifacts/foundation-m0-proof`
  completed an **OFFLINE** controlled synthetic run and forged-caller denial.
  Private local `offline-proof.json` plus deterministic source ZIP saved in that
  ignored directory. Transcript: 2 fake model responses, 1 fake tool call, 6 fake
  Gateway operations; reported 200/40 input/output tokens are **fixture values**.
  Actual provider calls **0**, actual provider usage **NOT_RUN**, cost **unknown**,
  real OTel export/readback **NOT_RUN**. Further source changes require regenerating
  final package digests; these artifacts are not the final release artifact.
- `git log origin/main..HEAD --oneline` was attempted and failed:
  `fatal: ambiguous argument 'origin/main..HEAD': unknown revision or path not in the working tree.`
  Remote default is `feat/local-first`; no `origin/main` exists locally after
  successful fetch. Ownership will be checked against the explicitly approved
  `d0612a6` input plus the real remote default/feature branch before push.

## Milestone 6: live provisioning failures investigated

Owned stack reached **CREATE_FAILED**, with 12 resources complete (both Gateways,
Tools target, Policy engine, 4 roles, synthetic Lambda, Evidence bucket/TLS policy
and Operations log group). Model target and tool policy failed. Exact sanitized
failure evidence:

```text
Properties validation failed for resource ToolPolicy with message:
#/PolicyEngineId: expected maxLength: 59, actual: 91
#/PolicyEngineId: failed validation constraint for keyword [pattern]

Resource handler returned message: "GatewayTarget DVWGC1CJIF failed to stabilize,
status: FAILED, reason: Failed to discover models from inference provider for
target DVWGC1CJIF. Error: Inference list-models call to
https://bedrock-mantle.us-west-2.api.aws/v1/models failed with HTTP 403:
{"error":{"code":"access_denied","message":"User:
arn:aws:sts::[ACCOUNT]:assumed-role/gab-foundation-m0-model/inference-iam-auth-session
is not authorized to perform: bedrock-mantle:ListModels on resource:
arn:aws:bedrock-mantle:us-west-2:[ACCOUNT]:project/default because no identity-based
policy allows the bedrock-mantle:ListModels action","param":null,
"type":"permission_denied_error"}}"
(RequestToken: e87cddba-00aa-96ad-b5e1-906f3b513556,
HandlerErrorCode: NotStabilized)
```

Root causes confirmed with official contracts: PolicyEngine `Ref` returns ARN;
`Fn::GetAtt PolicyEngineId` returns the required ID. The Mantle IAM service prefix
is **bedrock-mantle**, distinct from bedrock. Official service authorization
reference permits metadata-only `ListModels` scoped to `project/default`.
`CreateInference` is a different action with a Model condition key. Planned
correction is restricted to these owned resources: fix the ID, allow catalog
discovery only, explicitly deny Mantle inference, retain all other guards.
No inference permission will be granted. The 403 is not a reason to bypass IAM.

Source: https://docs.aws.amazon.com/service-authorization/latest/reference/list_bedrock-mantle.html
and https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-policyengine.html
retrieved 2026-09-12 Sydney.
No direct tool/model/Runtime/Browser invocation; actual provider usage **NOT_RUN**.

Corrective template regression was reproduced (1 failing test), then corrected;
focused checks **86 passed**. First corrective ChangeSet
`foundation-27f1a8d158d9f4ff5e0842d53858dd98` was **NOT_EXECUTED**:
strict inspection returned `RuntimeError: CHANGESET_RESOURCE_SET_MISMATCH`.
Inspection showed tag removals (Tags omitted on update) and replacements of the
two failed placeholders. Correction preserves current tags and permits replacement
only after inventory confirms exactly ModelTarget/ToolPolicy are CREATE_FAILED.
No healthy resource replacement, Remove, shared-resource mutation or inference
grant is allowed. Unexecuted ChangeSet retained for review.

## Milestone 7: full verification and final reusable artifact

- Full command: `uv run --locked --extra foundation-runtime pytest -q`
  with a permitted temporary uv cache. **490 passed**, the same 3 baseline
  deprecation warnings, **18.14s**. Existing CI installs this optional dependency
  set so its tests can import JSON Schema and OTel libraries; no new CI deployment.
- Local code review tightened OTel resource metadata to exclude environment
  injection and set an explicit owned log-group destination. Trace payloads and
  HTTP transmission are bounded; the scoped X-Ray log delivery policy and
  transaction-search state remain live gates, not presumed configured.
- Final offline driver:
  `python -m scripts.foundation_probe --output-dir artifacts/foundation-final-proof`.
  **OFFLINE_PASS** with pre-dispatch forged-caller denial and actual executor loop.
  Local proof file and source ZIP are private/ignored; no AWS upload.
- Foundation source digest:
  `aefe1feaeb7e175ffe7e55d93bacd953225606eeba7975c4b734d31615352b2f`
- Immutable manifest digest:
  `53d0e6ade8abeaae39058e772adacea270638f6ea0a9e9378620a68cb91e174e`
- Source package digest:
  `b4580d3500eac6e4f139ccc877d9707ba5d7f4f1b239014abb360177751d4004`
- Source package requires Linux ARM64 dependency assembly before deployment.
  No built/deployed image, Runtime or live trace is claimed. Two-config unit proof
  shares the source digest; **same-image live two-config acceptance NOT_RUN**.
- `git diff --check` and changed-file account/forbidden-service-call scan pass.
  653 lines in the owned foundation package; no business web-research imports.
  No other working-tree changes were present at start or discovered during work.
- Tagged repair ChangeSet
  `foundation-27f1a8d158d9f4ff5e0842d53858dd98-repair` prepared, inspection pending.
  Inference attempts remain **0**, real provider usage **NOT_RUN**, billed cost
  **unknown**, definition-provisioning reservation **$0.001920** retained.

Tagged repair inspection initially returned the same scope mismatch. Read-only
inspection confirmed CloudFormation recovery lists unchanged tag/attribute
dependencies across the owned stack even with identical Tags. The repair checker
now verifies current/ChangeSet tags are identical, the initial live template has
the exact recorded digest, desired property differences remain only the reviewed
correction, and other owned entries are only non-recreating tag/resource-attribute
dependencies. Direct property changes outside the correction remain denied.
Only the two currently verified CREATE_FAILED placeholders may be replaced.
No rejected ChangeSet was executed by bypassing these checks.

Final repair inspection **PASSED**: `CREATE_COMPLETE / AVAILABLE`, template digest
`27f1a8d158d9f4ff5e0842d53858dd98b00a7b7d785c244260d85e1bcb14457a`.
The 14 owned Modify entries contain the reviewed direct correction plus unchanged
tag/resource-attribute dependencies. Only ModelTarget/ToolPolicy are replacements;
healthy resources remain in place. Target, original template, inventory and tag
checks passed. Focused suite after this guard refinement: **87 passed**, 2 baseline
warnings, 2.95s. Corrective execution is pending at this record.

## Milestone 8: actual owned stack complete

Repair executed through the validated ChangeSet. Final CloudFormation status
**UPDATE_COMPLETE**. All 14 declared resources are complete: TWO dedicated
Gateways, exact Claude target, synthetic Lambda tool target, Policy engine/tool
policy, four scoped roles, synthetic Lambda, private Evidence bucket/TLS policy
and seven-day Operations log group. Only the two failed owned placeholders were
replaced during recovery. No shared resources or healthy resources were deleted.

Live read-only metadata already verified:

- Model Gateway `gab-foundation-model-m0-gm7vvru7gn`: READY, AWS_IAM, ENFORCE.
- Tools Gateway `gab-foundation-tools-m0-nvfpwjzcjm`: READY, AWS_IAM, ENFORCE.
- Tools target `fixture` / `2TZZPPC0FJ`: READY.
- Evidence bucket `governed-agent-builder-foundation-m0-evidence-qu9wwuo37h21`:
  AES256, Versioning Enabled, all four BPA flags true; no content uploaded.
- `/governed-agent-builder/foundation-m0`: retention **7 days**.
- All four owned roles have **zero attached managed policies**.
  Fixture role has no execution permissions. Foundation role has exact Gateway
  invoke + X-Ray trace-write permissions, no admin/direct-provider/Browser grants.
  Final role-policy/target readback after repair is pending.
- Real metadata read request IDs: CloudFormation
  `8bbae209-a41e-4ee2-873c-0feefcd3c63f`; Model Gateway
  `0f309640-3487-433e-81e1-2b8f0ee2fc85`; Tools Gateway
  `9f2f76a8-3221-428d-86a7-a20d2acc75eb`. These are **metadata requests**, not
  inference or trace evidence.

Final local review caught a missing reservation check at the real transport
boundary. A regression test reproduced it without networking, and model/tool/
OTel adapters now require a reservation and a one-inference M0 budget before
real dispatch. OTel additionally caps export count at five. Source digests from
Milestone 7 are superseded pending regenerated release proof. Production entry
remained blocked throughout; no paid inference escaped.

## Milestone 9: final readback and configure-before-deploy proof

- Completed-stack read request `b35cd754-dad6-4a84-8fef-eb055d4d4577`.
- Model target `claude` / `5ADTYKAJSO`: READY, configuration digest
  `71be435a61f4e76199d5076f9028cc609ec92e8707c1fb5fd1551996241b3792`;
  read request `17480b25-be86-4837-a15e-20097e6061e2`.
- Tools target `fixture` / `2TZZPPC0FJ`: READY, configuration digest
  `91e32328e88b27dbc0215830b9d6ee47304e296a3ebc3d63c420bf45e290574b`;
  read request `19d8a903-6663-4238-8c57-3043e34b5040`.
- Policy `synthetic_lookup-2sb3g0fbhn`: status/enforcementMode ACTIVE;
  read request `11683aed-1e7f-46b4-a3f5-2ccd5af74180`.
  Policy validation was submitted with FAIL_ON_ANY_FINDINGS; GetPolicy does not
  return that validationMode field. This validates the tool schema configuration,
  **not tool invocation or inference Policy coverage**.
- Final IAM readback confirms Model role only adds exact-project ListModels and
  explicitly denies CreateInference; Foundation role explicitly denies
  Bedrock/Mantle/Browser/command operations and has no admin grants.
- Account-free final metadata saved privately in
  `artifacts/foundation-final-cloud-proof.json`. No trace ID is fabricated from
  these real control-plane request IDs.
- Final offline executable proof in `artifacts/foundation-release-proof/`:
  forged caller denied before dispatch, generic model/tool loop SUCCEEDED with
  synthetic replies. Its 200/40 tokens are fixture values, not provider usage.
- Actual Gateway/schema-bound configuration and source ZIP saved privately
  (0600), with one inference maximum, using the digests in Latest verified result.
  No Runtime or running shared generic agent was created. No prompt override or
  synthetic production-auth switch was introduced.
- Final **491 passed / 88 focused**, full suite 16.71s. No further code changes
  are planned in this increment. Frontend build/e2e **NOT_RUN** (UI untouched).
- Remaining milestones and cleanup due **2026-09-19 Sydney** are documented in
  [CUSTOM-FOUNDATION-SLICE.md](CUSTOM-FOUNDATION-SLICE.md). Cleanup NOT_RUN; owned
  proof resources retained for review. No Browser/evaluator/judge/UI acceptance.

## Milestone 10: publication receipt

- Code/proof implementation commit:
  `aa91601e72158a7eabc79d9fe7d02e23ab728c1d`.
- Managed git authentication push **succeeded**:
  `d0612a6..aa91601 HEAD -> feat/live-capabilities`.
  GitHub API reconfirmed the existing repository is **private** before push.
- Required `git log origin/main..HEAD --oneline` was attempted again; that ref
  does not exist. The actual `origin/feat/local-first..HEAD` comparison and exact
  `d0612a6..HEAD` check showed only the approved baseline history plus this one
  implementation commit. Remote feature branch was still exactly the approved
  baseline before push. No unexpected commits, rebase, force push, PR or merge.
- This final documentation-only receipt follows the verified implementation.
  The 491-test result and cloud readback above apply to unchanged implementation
  code. No further cloud writes, inference, tool/Runtime calls, Browser traffic,
  evaluator calls or live UI enablement occurred during publication.

## Milestone 11: saved Studio/Foundation wiring finalized (source only)

Resumed the legitimate dirty increment at `7e5163690bc8b46c067cb21c887f4816352534ba`. No prior worker was
running. `/tmp/gab-wiring-tests-final.log` already reported 510 passed in 23.05s;
the earlier orchestration timeout was not a demonstrated pytest hang.

Implemented opt-in Studio API -> FoundationJobs -> create-only deployment,
READY continuation, pinned Runtime version/session dispatch, and persistent
run/reservation/call claims. Public UI submission remains fixture-only; it now
renders live-job terminal/progress states without unlocking fixture invocation.
Live is disabled by default, never silently replaced with fixture success.

Self-review (not independent review) checked the serializable DynamoDB fence,
current owner/workspace/version/grant/policy/session revalidation, immutable
manifest/role/runtime binding, and irreversible invocation claim before I/O.
Tightened RuntimeHandler to require the reservation handle to equal its run,
and made malformed API Gateway IAM principal structures fail closed. Exchange
identity comes only from the dedicated integration's
`requestContext.authorizer.iam.userArn` string, never body or payload headers.
The assumed-role ARN is matched against the current server-owned run role.
This is an offline HTTP API AWS_IAM integration contract, not proof that a caller
cannot forge an event through an improperly granted direct Lambda invoke.

Execution success is not release PASS: current-version trace readback, matching
trace ID, dataset/rubric/policy/manifest/runtime bindings and completed evaluation
IDs are mandatory. Unknown outcomes retain reservations; token reporting is not
an all-service cost or invoice. No paid-call retry or budget release was added.

Verification of final source:
- `uv run --locked --extra foundation-runtime pytest -vv --durations=10`:
  **519 passed**, three existing dependency deprecation warnings, **24.59s**.
  Wrapper timeout 150s, verbose output identifies the current test if interrupted.
- `cd frontend && npm run build`: **PASS** (includes TypeScript noEmit);
  existing >500 kB bundle warning remains.
- Existing Playwright hosted entry and current-user menu regressions: **2 passed**
  in 4.6s, local intercepted API contracts only, not hosted/cloud acceptance.
- Regressions cover malicious/malformed principal, forged headers, stale grants,
  cross-workspace/version/session, atomic reservation conflict, duplicate jobs,
  call replay, uncertain Runtime outcome, missing/cross-run reservation, and
  missing or mismatched version/trace/evaluation evidence.
- `git diff --check` and changed-source account/credential-literal scan: **PASS**.

Remaining release blockers: dedicated AWS_IAM route and exact Lambda resource
policy, reviewed per-package role and backend-only Runtime invoke permissions,
Linux locked-dependency runtime artifact build/readback, cloud trace/evaluation
reader, evaluated catalog-to-manifest factory and approved pricing envelope.
`configured_jobs` deliberately has no production evidence reader, so it cannot
produce LIVE_PASS. The package helper remains source-only; artifact admission
requires actual runtime dependencies. No Runtime created, IAM/model-role Deny
relaxed, cloud write, live enablement, PR, merge or force push in this increment.

Well-Architected self-review: security is fail-closed with cloud IAM unproven;
reliability has durable claims/unknown-outcome holds but no live recovery proof;
performance is bounded (one inference, finite time/calls); cost holds a reviewed
all-service reservation without claiming measured billing; operations expose
stages/evidence but still lack production reader; sustainability avoids retry
loops and new resources, with no measured environmental claim.

Peer reproduction (no AWS calls required):
```sh
git fetch origin feat/live-capabilities
git show --stat origin/feat/live-capabilities
uv run --locked --extra foundation-runtime pytest -vv --durations=10
uv run --locked --extra foundation-runtime pytest -q tests/test_foundation_wiring.py
(cd frontend && npm run build)
(cd frontend && npm run test:e2e -- hosted-entry.spec.ts --grep 'hosted Agent Studio entry|hosted current-user menu' --global-timeout=60000)
git diff --check HEAD^ HEAD
```
`origin/main` is absent in this clone; publication checks use the exact saved
base and `origin/feat/live-capabilities..HEAD`. Only the requested source increment
and this appended status are intended for publication on that feature branch.


## Milestone 12: admission source publication, 2026-09-12

Resumed exactly `c684c53af2b8e91169f7fc29ffa637aa4ae5192b`; existing legitimate
changes preserved. Process inspection found no project-specific writer (other
interactive coding sessions belong to other directories). No nested workers/CC.
Private GitHub repository and remote feature tip verified at that exact base.
`origin/main` is absent; compare approved base, actual default
`origin/feat/local-first`, and remote feature branch instead.

Self-review covers dedicated AWS_IAM HTTP API route, exact API/stage/method/path
and payload-v2 namespace, trusted `authorizer.iam.userArn` rather than headers,
server-owned role/manifest binding, current session/owner/workspace/version/epoch
revalidation, capability/resource allowlist, replay claims, immutable
agent-version Runtime reuse, and response Runtime/version binding.
All IAM event fixtures are OFFLINE tests, not proof of real gateway context.
The Lambda resource-policy Allow is scoped to the exact API/stage/POST ARN and
source account. It alone does NOT prohibit same-account IAM principals with
identity-based direct Lambda Invoke permission from synthesizing an event;
workload role explicit direct-invoke denial and actual signed route verification
remain mandatory before production admission. No end-to-end per-session
workload identity isolation is claimed from the unit fixtures.

534 tests passed in 30.55s with 3 baseline warnings in the previous worker's
final log; implementation unchanged during this review, so no redundant full
rerun. Diff whitespace check PASS. Previously recorded frontend build and 2
local intercepted browser regressions are not fresh hosted acceptance.

Deterministic locked Linux ARM64 Python 3.13 dependency ZIP:
`artifacts/foundation-admission/runtime-final.zip` and `runtime-final-repeat.zip`.
SHA-256 `3821b5ca17b76b1801c225723b45f14b691dacb55bd4c6c572a5e404b1a2ae82`.
Source digest `6bbeaf313321abc9da4861d539fa1ffd979140dd379b30e2e6efb3e9817a86ca`.
Manifest `85e417cfa752684cc508d7e8698284a0202f17b1f93930454aec948371479c07`.
Proof: `artifacts/foundation-admission/package-proof-final.json`; packaging log:
`/tmp/gab-runtime-package-final.log`. Host entrypoint PASS is host Python only.
Linux execution UNVERIFIED: Docker container start returned PID 0. Peer Linux
ARM64 import/entry verification is required; no actual Runtime acceptance.
Artifacts are private local ignored files, not remotely available via git.

At this publication: cloud admission route NOT_DEPLOYED/NOT_VERIFIED; no cloud
write in this follow-through yet. No model calls, Runtime create/invoke, new
accounts, password/SSM reads, Cognito changes, UI live enablement, PR or merge.
CreateInference Deny preserved. Existing fixture journey stays unchanged.
Cloud live catalog, evaluator/evidence reader, UI and Browser are next slices,
not delivered by admission source.

Well-Architected: Security gated by real IAM/direct-invoke checks; Reliability
offline-tested with durable holds, live unverified; Performance live unmeasured;
Cost no paid calls and no invoice claim; Operations source/log/package evidence
available but cloud admission unverified; Sustainability bounded serverless
scope, no new always-on resource. Memory recall service timed out; this record
uses the repository and supplied on-disk evidence, not recalled status.


## Milestone 13: additive backend admission deployment receipt

Implementation SHA **`28c53db6e545f82931fe3fb3b5fe577da6390358`** verified on private
`origin/feat/live-capabilities`. This receipt changes documentation only.
Source/package verification repeated using project Python: both final Runtime
ZIP hashes match, source digest matches, and all embedded foundation source
files match the committed tree. An initial system-Python digest import failed
with missing pydantic; project `.venv/bin/python` verification succeeded.
No implementation changed after the recorded 534-test run.

Cloud target was verified live before mutations and after completion: approved
profile, us-west-2, STS account equals existing CloudFormation/CloudFront/Cognito
identities, exact Studio domain. No account IDs or credentials were printed.
`governed-agent-builder-serverless-app`: **UPDATE_COMPLETE**.
ChangeSet `foundation-admission-1789143495` reviewed with exactly six **Add**
entries: FoundationExchange, FoundationExchangeRole, FoundationExchangeLogs,
FoundationExchangeIntegration, FoundationExchangeRoute, FoundationExchangePermission.
No Modify/Remove/replacement/dependency exception; original resources and all
stack outputs remain unchanged. Existing app Lambda code and WorkerRole were
NOT updated: the narrow update overlays only six new resources onto the actual
live template, with a dedicated content-addressed Code value. The complete
generated source template is therefore NOT what was deployed wholesale.

Backend artifact SHA-256:
`690a997cf61fbc0078edb3cd53c3e47cde36868bae88e0a4a6ce1302d098b2c7`.
Uploaded only to the existing project's retained private release bucket at
`releases/<backend-sha256>/foundation-admission.zip`; S3 bytes read back and
hash matched. Lambda CodeSha256 matched, State Active, LastUpdateStatus
Successful. Runtime ZIP was NOT uploaded or executed. BPA checked true and
unchanged. No identity/data/user/credential/website resource mutation.

Actual route: `POST /internal/foundation/exchange`, AWS_IAM, on the existing
API Gateway endpoint (not a new CloudFront path). Lambda resource policy read
back as one Allow for apigateway.amazonaws.com, source account exact and
source ARN exact API /$default/POST/internal/foundation/exchange. No broad
Lambda invoke permission was added.

HTTP probes (no human login, no password):
- Existing Studio `/`: **200**.
- Existing Studio `/api/agents`, anonymous: **401**.
- New exchange, anonymous: **403**, not requested 401; preserve actual AWS_IAM
  behavior rather than replacing IAM or adding a permissive anonymous route.
- New exchange, unsigned spoofed principal/context headers: **403**.
- New exchange, existing operator SigV4 identity and nonexistent run: **403**,
  response `ADMISSION_DENIED`. This proves a signed request reached the
  handler denial surface, NOT successful redemption or a verified workload
  principal/unknown-run branch (the handler intentionally shares denial codes).
- Existing foundation role direct lambda:InvokeFunction policy simulation:
  **implicitDeny**. This is simulation only, not an actual workload invocation
  denial, and not an explicit Deny.

Concrete acceptance blockers: existing FoundationRole trusts only
`bedrock-agentcore.amazonaws.com`, has no exact exchange Invoke grant, and
there is no admitted Runtime/approved workload credential to perform the
requested signed workload probe. Do not manufacture requestContext, create
new test accounts, widen its trust to the operator, or treat operator signing
as workload proof. Workload direct-invoke hardening/exact role grant remains
a next bounded IAM step. Per-run/session/runtime/version binding is offline
verified only; cloud end-to-end acceptance remains **NOT_READY**.
Linux ARM64 runtime execution still UNVERIFIED (Docker PID 0), peer required.
CreateInference Deny and disabled live UI remain intact. No paid inference,
Runtime creation/invocation, evidence-reader/live-catalog/evaluator/Browser
acceptance performed. Existing fixture assets/backend were not replaced;
authenticated fixture journey was not rerun without human session access.

Private local proof: `artifacts/foundation-admission/cloud-update-proof.json`,
`cloud-inspection.json`, `package-proof-final.json`; logs:
`/tmp/gab-admission-cloud-update.log`, `/tmp/gab-admission-backend-package.log`.
The bounded executed driver is `/tmp/gab-admission-cloud-update.py`; no timer
or background worker remains scheduled. Proof directory is ignored by git.
Well-Architected delta: Security now has actual IAM route/resource-policy
readback but workload acceptance blocked; Reliability stack complete/old
resources unchanged; Performance no live workload measurements; Cost no model
calls, infrastructure/API probe charges unknown; Operations has deployed-code
hash and negative HTTP evidence; Sustainability six bounded serverless
resources, no new always-on service.

## Milestone 14: exact admission role policy source; live ChangeSet blocked

2026-09-12 02:30 AEST. Resumed verified clean `5e39165eb82d471a2a0ee582427795f0600e99e8`.
Published implementation `a7c936e85eba4fdd706bb553affb4710a6378624` before live prepare.
New `scripts/admission_role_policy.py` overlays only FoundationRole inline
Policies onto the LIVE template, retaining every other property/resource and
AgentCore-only trust. It grants only exact existing API /$default/POST/internal/
foundation/exchange execute-api:Invoke and explicitly denies lambda:InvokeFunction
on all functions (the wildcard is a DENY, never trust or Allow). Existing
CreateInference/model/browser denies remain unchanged. The bounded reviewed
prepare/execute driver is `scripts/admission_role_update.py`; it refuses any
ChangeSet outside one non-replacing FoundationRole.Policies modification.

Verification: 45 targeted admission/delivery/policy tests passed; full suite
**543 passed in 32.62s**, three existing dependency warnings. After enhancing
blocker persistence only, 9 policy tests passed again. git diff --check passed.
Feature publication inventory contained exactly our one implementation commit;
origin/main remains absent, so origin/feat/live-capabilities was the comparison.
No PR/merge. Memory recall timed out; repository source and live metadata used.

Live StudioTarget checks before and after prepare: **MATCH**, approved profile
agentic-platform-prod/us-west-2, STS account matches existing Studio stacks,
CloudFront and Cognito internally; account IDs not printed. AWS_IAM exact route
was checked. ChangeSet `foundation-role-admission-1789144065` CREATE_COMPLETE /
AVAILABLE, **NOT EXECUTED**. AWS reports TWO modifications:
- FoundationRole.Policies: direct, static, RequiresRecreation Never.
- ToolPolicy.Definition: dynamic ResourceAttribute dependency on
  FoundationRole.Arn, RequiresRecreation Never.

Both are non-replacing, but the second violates the requested exact-role-only
execution gate. No dependency exception was silently introduced. The guard
stopped with FOUNDATION_ROLE_ONLY. Foundation stack remains UPDATE_COMPLETE;
this is its previous stable state, NOT a receipt of this update. The new exact
route grant and direct-Lambda explicit Deny are **source-tested, NOT DEPLOYED**.
No workload principal or live allowed/denied run evidence was obtained. Signed
operator/anonymous results from milestone 13 do not become workload proof.
Receipt: private ignored artifacts/foundation-admission/role-update-proof.json.
Actual CreateChangeSet request ID: 10cb50de-6669-4442-b801-177222a0ee4f.

Artifact: runtime-final.zip SHA-256 still
3821b5ca17b76b1801c225723b45f14b691dacb55bd4c6c572a5e404b1a2ae82;
all embedded foundation source bytes match current source, source digest
6bbeaf313321abc9da4861d539fa1ffd979140dd379b30e2e6efb3e9817a86ca.
Actual Linux ARM64/Python 3.13 import remains UNVERIFIED; no docs/PEER-LINUX-QA
receipt present during this slice. No Runtime was created as a substitute for QA.
The requested typed synthetic service-probe context and isolated probe entry
are NOT IMPLEMENTED in this slice. Existing human hosted-session checks were
not bypassed; no synthetic grant/job/session or user records were written.

User authorization supersedes the old circular handoff constraint: an isolated
admission-only probe Runtime is permitted AFTER Linux artifact validation and
reviewed narrow IAM/caller bindings, before workload proof (two-phase create
then bind actual ARN/version, prepare protected record, then invoke). This does
not authorize full Runtime/harness production enablement. Do not infer that the
current full engine artifact is a no-inference probe. Need isolated deterministic
probe source, real Linux validation, protected typed service context, scoped
operator invoke policy, real-role allowed plus cross-run denied receipts.
The immediate cloud blocker is the extra ToolPolicy dependency; needs explicitly
reviewed resolution that honors scope before any execute, not a generic retry.

This slice counts: Runtime creates 0, Runtime sessions 0, Runtime invocations 0,
model/tool/Browser/eval calls 0; one ChangeSet created and retained unexecuted for
review, no timer/worker scheduled. Runtime execution duration not applicable;
actual service charges unknown, no zero-cost/invoice claim. No S3/SSM/Cognito,
trust, user data, fixture UI or inference-deny changes. No live harness PASS.

Well-Architected: Security fail-closed guard stopped broader cloud mutation;
Reliability persists exact unknown/blocked states without retrying writes;
Performance no Runtime timing evidence; Cost no workload execution and unknown
control-plane charges; Operations published source, test and ChangeSet receipts;
Sustainability no new running/always-on workload. Full harness/evidence-reader,
evaluation, custom-input digest acceptance and Browser remain NOT PROVEN.
