# Studio diagnostic release: peer-only deployment handoff

Target: https://de32ssfw7gsad.cloudfront.net/; account suffix 1711, us-west-2.
Cloud writer: didi only. This release assembles code; it does not authorize paid invocation, mint approval, or claim Ready.

## Exact source composition
Base `7c49dcbd617215909a425d9d3a15c04b43c4db0d`, containing backend `60aa14e`, adapter `9540be4`, transport `154a811`, transport ARM evidence `37e1559`, admission `86f852d`, isolation `7c49dcb`. Cost commits `4cdf48c` and `14bf01f` cherry-picked as `838354a` and `19a52ae`. No late `c188020` transport swap. UI `82e65a8` and general-requests IAM `ded092a` are ancestors and unchanged by this release.

Changes:
1. `scripts/serverless_package.py` includes `scripts/opus_capture_ticket.py`, previously missing despite diagnostic backend imports.
2. `scripts/package_diagnostic_capture.py` packages the diagnostic entry/client with locked ARM dependencies, immutable settings and no repository or production admission file. It does not use a synthetic product manifest.
3. `infra/diagnostic_capture.py` adds exactly six resources to a fresh deployed app template, preserving every existing property: one dedicated Lambda, one Lambda-only role, one log group, one integration, one exact AWS_IAM route, one exact API Gateway Lambda permission. No browser route/Function URL.
4. `scripts/assemble_diagnostic_release.py` emits the additive template offline, refuses replacement/collisions, requires versioned backend artifact and exact owned Runtime/endpoint ARNs. Service defaults disabled.
5. `runtime_isolation_policy` emits the exact route grant plus explicit DynamoDB/AssumeRole/PassRole/direct-service-invoke denies. Existing model permissions and direct-provider denies remain separately preserved. This function is a policy fragment, not a whole role replacement.

## Fresh readback, not assumed missing infrastructure
Read-only Studio ownership verification passed. App and Foundation stacks are stable. Only `AgenticPlatformRuntime` exists; no dedicated Foundation Runtime exists. Do not reuse that shared Runtime.
An existing `governed-agent-builder-network` VPC was found, with private-address subnets, an existing NAT-routed table and a no-inbound project security group. Thus do NOT default to building a VPC/NAT/endpoints or ask the user for a new network before checking compatibility. Exact IDs/route associations/security-group contents are retained only in the worker's private local readback and must be refreshed by peer. NAT availability, subnet association, SG egress and endpoint reachability still need peer verification. Existing versioned artifact bucket and dedicated Foundation role were already resolved in peer cost evidence.

## Exact build and assembly commands (peer)
Run from the release branch. Commands below are build/assembly, not implicit cloud mutation authorization.

```sh
python -m pytest -q tests/test_diagnostic_release.py tests/test_diagnostic_exchange.py --disable-warnings --maxfail=2
python -m scripts.serverless_package
python -m scripts.package_diagnostic_capture \
  --settings "$PRIVATE/capture-settings.json" \
  --dependencies "$LOCKED_ARM_DEPENDENCIES" \
  --output "$PRIVATE/diagnostic-runtime.zip"
python -m scripts.assemble_diagnostic_release \
  --current-template "$PRIVATE/current-app-template.json" \
  --bindings "$PRIVATE/service-bindings.json" \
  --output "$PRIVATE/diagnostic-app-template.json"
```

`capture-settings.json` requires exactly `region=us-west-2`, `exchange_endpoint=https://<actual-api-id>.execute-api.us-west-2.amazonaws.com/internal/diagnostic/capture`, `manifest_digest=<reviewed diagnostic manifest SHA256>`. This digest links a reviewed diagnostic manifest, not product Ready or response allowlist.

`service-bindings.json` requires exactly `bucket`, `key` (approved/...zip), `version` (non-null immutable backend S3 version), `runtime_arn` (new owned gab_foundation_ Runtime), `endpoint_arn` (its exact runtime-endpoint ARN). Real IDs are obtained from AWS, never guessed. Peer uploads exact build once under the existing approved prefix and records version/hash. Existing app artifact parameters remain unchanged, so the six-resource service addition does not roll older backend code onto unrelated Lambdas.

Peer CloudFormation deployment sequence, once exact bindings are populated:

```sh
aws --profile agentic-platform-prod --region us-west-2 cloudformation create-change-set \
  --stack-name governed-agent-builder-serverless-app \
  --change-set-name studio-opus-diagnostic-service \
  --change-set-type UPDATE \
  --template-body "file://$PRIVATE/diagnostic-app-template.json" \
  --parameters "file://$PRIVATE/use-previous-app-parameters.json" \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM
aws --profile agentic-platform-prod --region us-west-2 cloudformation describe-change-set \
  --stack-name governed-agent-builder-serverless-app \
  --change-set-name studio-opus-diagnostic-service
```

Inspect exactly SIX Add changes, zero Modify/Remove/Replacement; compare fresh current template digest to assembly base; preserve all existing parameters with UsePreviousValue, tags and service role. Confirm deployed IAM action/resource support before execution. Any drift or extra change stops apply. Peer alone then executes the reviewed named change set and reads back route auth, exact permission and role policy. Lambda remains disabled pending capture authority.

Runtime create payload must include ONE new `gab_foundation_*` Runtime, immutable diagnostic code S3 bucket/prefix/versionId, PYTHON_3_13/main.py, existing compatible dedicated role, VPC networkConfiguration using verified existing private subnet/SG, environment DEFINITION_DIGEST and MANIFEST_DIGEST, and lifecycleConfiguration `{idleRuntimeSessionTimeout:60,maxLifetime:60}`. Obtain actual runtime ID/version/endpoint from service response. Do not regenerate whole Foundation stack, update shared Runtime, broaden grants, or remove existing Haiku permission. Creating the Runtime does not grant callers permission to invoke it. Before capture, bind exact one Runtime/version to the workload role; service receives GetAgentRuntime/GetAgentRuntimeEndpoint only on those exact resources.

## Concrete bounded activation envelope for review, NOT approved funds
Proposed rollout reserve: USD 1 total, split USD 0.50 diagnostic capture and USD 0.50 first Studio answer, retained on success/unknown. This is a conservative risk allowance proposal, NOT an asserted all-service upper bound and NOT yet user-approved budget. If an existing applicable approved project allowance covers this, record its evidence and reserve from it; otherwise ask ONE decision: authorize this USD 1 bounded rollout, choose another amount, or leave disabled. Do not mistake the existing USD 5 code cap for approval.

Resource count: one owned Runtime; one diagnostic Lambda at 512 MiB, timeout 15s, reserved concurrency 1; one dedicated service role; one diagnostic log group retention 1 day; one integration/route/permission; zero new VPC/NAT/bucket/table; reuse existing API/state/artifact infrastructure only after compatibility checks.

Proposed execution limits: one capture ticket and one later separately authorized product request; output <=256, thinking disabled, no retry/fallback; <=4096 serialized UTF-8 input bytes (not a provider token count); one Runtime session per phase with 60s lifetime plus termination grace; at most three exchange requests per capture, four Runtime control reads, one STS lookup. Bound request tokens using provider-compatible evidence before paid send. Admission currently checks ticket and funds but does NOT enforce every lifecycle/input/global invocation/log-volume proposal. Peer must implement/read back remaining limits, not set verified booleans. Bounded runtime logs proposal: 1 MiB ingestion risk allowance, no content logging, dedicated group retention 1 day; restricted receipt <=4096 bytes; artifact/evidence prefix current/noncurrent expiry 1 day merged into existing lifecycle, plus explicit allowance for asynchronous expiration. No claim that retention caps ingestion or that Lambda concurrency caps lifetime requests. Shared API logs and Dynamo full-partition transaction fanout remain in the allowance/quantity review.

## Exact remaining activation gate
**Protected, reviewer-audited capture authority bound to the final Runtime/artifact and an applicable funded reserve.** The current code ships a consumer, not an operator approval producer. An authenticated operator workflow must write the real authority/runtime/pricing/source/review records and matching audit; fixture records and boolean approvals are forbidden. This gate cannot be replaced by a ZIP or a deployment flag. Peer owns that final binding and capture-window freeze. A paid call stays denied until identity, pricing, permissions and funding are valid.

After capture, retain unverified observed-model evidence; independently review it, pin actual response identity into the production Model contract, build/admit production artifact, and update only the newly owned Runtime under exact-ID approval. First real Studio answer is a separate authorized invocation, not proof from diagnostic capture. Record deployed code hashes/version timestamps, real authenticated answer/run receipt and Haiku/UI/general-request checks. No paid call, live QA or Ready is claimed by this delivery.

## Review posture
Security: exact AWS_IAM route and no Runtime settings privilege; actual IAM isolation still readback-gated. Reliability: persistent no-replay holds, no retry, default disabled. Performance: new service concurrency/time bounded, full-partition state scale still needs review. Cost: explicit proposed reserve and known limits, approval/enforcement gaps named. Operations: additive changes, immutable artifacts, peer-only apply, exact rollback. Sustainability: one owned Runtime, reuse compatible infrastructure, two separately admitted model requests maximum in proposed rollout.

Rollback: disable new diagnostic admissions first; retain claimed/unknown holds. Remove only six newly added resources after evidence export and appropriate retention; detach only the new role-policy fragment using drift-safe merge. Preserve shared app/Foundation resources, Haiku, UI, IAM general_requests and all unrelated policy statements. Do not wholesale restore stale templates over newer peer work.
