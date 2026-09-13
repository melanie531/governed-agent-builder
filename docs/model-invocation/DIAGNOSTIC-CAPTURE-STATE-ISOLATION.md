# Diagnostic capture: service-mediated state boundary

Status: CODE ONLY, DISABLED, NOT CALLABLE. No deployment authorization.
Base: `86f852d4f7b9a06cb17513f4d65605e5462719c2`.

## Boundary

`runtime.diagnostic_capture.invoke` now composes `DiagnosticExchange`, not
`DynamoStore`. Immutable settings contain only `region`, `exchange_endpoint`,
`manifest_digest`; no settings file is shipped. One boto3 workload session owns
STS identity, SigV4 execute-api calls, and the frozen SigV4 Model transport.
The service NEVER obtains or assumes that workload role or sends inference.

The existing product exchange, production Model allowlists/grants, codec,
transport, and ticket implementation are unchanged. Its handler accepts only the
product path and its IAM partition list omits diagnostic audit reads. Reusing or
broadening that route would mix authorities. A NEW dedicated diagnostic Lambda
integration/role and exact AWS_IAM route are required. Only the explicit disabled
handler seam is supplied; no infrastructure template is edited.

The trusted identity is API Gateway HTTP API v2
`requestContext.authorizer.iam.userArn`, normalized to an exact assumed IAM role.
Payload identities, headers, product `run_ref`, arbitrary keys/tables/actions,
and authority writer operations are rejected. This trust holds ONLY when direct
Lambda invocation is inaccessible to callers and the exact API invocation grant
below is independently verified. A fabricated event passed directly to Python is
not AWS authentication.

`reserve` resolves protected authority/review/pricing/runtime records server-side,
checks manifest and exact role, verifies Runtime/endpoint control readback, then
atomically reserves all account/workspace/user/agent holds and one ticket plus an
immutable resolved-binding digest. `claim` repeats admission and control readback,
checks that binding, and atomically consumes the ticket. The Runtime sends ONLY
after receiving the bound successful claim response, which follows transaction
commit. One capture reference is one ticket, including crashes and retries.

`complete` accepts only `UNKNOWN`, or `CAPTURED` with a 64-hex response digest and
an optional <=200-character unverified observed model. It revalidates admission,
exact caller/binding/owner and atomically finishes the ticket with restricted
server-derived evidence. It never accepts a receipt, request, cost, identity,
approval, or arbitrary evidence key from the Runtime. Completion cannot overwrite
evidence or release funds. Invalid/revoked/expired authority also denies completion;
this can leave CLAIMED without evidence, intentionally preventing another send.
An independently reviewed reconciliation procedure, not this API, would handle it.

Lost reserve response: holds and RESERVED ticket may remain; another reserve is
denied. Lost claim response: CLAIMED may remain, zero Runtime sends. Model timeout:
no retry, one UNKNOWN completion attempt. Lost/failed completion: CLAIMED or
CAPTURED remains, no retry or release. Transport/codec/ticket semantics are frozen.

## Minimal deployment diff proposal (NOT APPLIED)

All angle-bracket values below must be replaced by independently reviewed exact
values. They are not usable policy or settings artifacts.

1. Add `DiagnosticCaptureExchange` Lambda using
   `backend.serverless.diagnostic_capture_exchange_handler`, a NEW dedicated
   Lambda execution role, and the pinned reviewed backend artifact. The handler
   defaults off. Future reviewed environment ONLY:
   - `STATE_TABLE=<existing-authority-state-table>`
   - `DIAGNOSTIC_CAPTURE_API_ID=<exact-http-api-id>`
   - `DIAGNOSTIC_CAPTURE_EXCHANGE_ENABLED=0` until explicit activation approval.
   Do not change `FoundationExchange`, its flags, role, or route.
2. Add HTTP API AWS_PROXY integration (payload format `2.0`) to that function and
   route `POST /internal/diagnostic/capture`, authorization **AWS_IAM**, `$default`
   stage. No Cognito/Lambda-authorizer, `/api` wildcard, browser, or product route.
3. Lambda resource permission:
   - Principal `apigateway.amazonaws.com`
   - Action `lambda:InvokeFunction`
   - SourceAccount `<exact-account>`
   - SourceArn
     `arn:aws:execute-api:us-west-2:<exact-account>:<exact-api-id>/$default/POST/internal/diagnostic/capture`
   - No Function URL, wildcard API/stage/method/path, or direct Runtime/browser
     invoke grants. Audit same-account identity policies too: a narrowly scoped
     resource permission alone does not exclude IAM-authorized direct invoke.
4. Dedicated Runtime workload role:
   - Allow `execute-api:Invoke` ONLY on the exact route ARN above.
   - Remove ALL DynamoDB allows. Require an explicit `Deny` of `dynamodb:*` on `*`
     for this dedicated role (or an equivalently verified permissions boundary),
     so inherited policies cannot restore repository access. Runtime needs NO
     DynamoDB read/write permissions, including transaction condition checks.
   - Deny direct invocation of the diagnostic service function (exact function ARN
     and qualified ARN suffixes), and ensure no role-assumption/pass-role escape.
   - Preserve the independently reviewed exact Model permission and direct-provider
     denies unchanged. No new inference/production grant is proposed here.
5. Dedicated service role (Lambda trust ONLY, no Foundation assumption):
   - `dynamodb:GetItem`, `dynamodb:Query` on exact State table, with
     `ForAllValues:StringEquals` / `dynamodb:LeadingKeys` restricted to
     `_revision`, `settings`, `agents`, `versions`, `principals`, `audit`.
   - `dynamodb:ConditionCheckItem` on exact State table, LeadingKeys `_revision`.
   - `dynamodb:PutItem`, `dynamodb:UpdateItem` on exact State table, LeadingKeys
     `_revision`, `settings`. No DeleteItem, BatchWriteItem or table administration.
     DynamoDB authorizes TransactWriteItems constituent actions (Put/Update/
     ConditionCheck); do not invent a standalone IAM `dynamodb:TransactWriteItems`
     permission as a substitute. CAS-only read transactions need ConditionCheckItem.
   - `bedrock-agentcore:GetAgentRuntime`,
     `bedrock-agentcore:GetAgentRuntimeEndpoint` on the exact reviewed diagnostic
     Runtime resources, using the service's supported IAM resource types. Confirm
     exact version/endpoint resource ARN support during deployment review; do not
     silently replace unsupported version scoping with broad invoke privileges.
   - No Model invocation, direct-provider invocation, `sts:AssumeRole`, or
     `iam:PassRole`; preserve explicit denial of those privileges in the final
     service policy. Any required logging/network/cost configuration belongs to
     the peer and must be separately bounded/reviewed.
6. Bake Runtime settings only after review:
   `region=us-west-2`, `exchange_endpoint=https://<exact-api-id>.execute-api.us-west-2.amazonaws.com/internal/diagnostic/capture`,
   `manifest_digest=<reviewed-digest>`. Package new
   `foundation_harness/diagnostic_exchange.py` with Runtime; it needs no backend
   repository modules. Backend package needs `backend/diagnostic_exchange.py`,
   the handler, admission resolver, and existing ticket/repository dependencies.
   No package/config/artifact creation or upload is performed by this change.

**Honest isolation claim:** IAM separates the untrusted Runtime from the trusted
state service; IAM does NOT isolate settings sort keys within the service. The
service can write the settings partition, just as other trusted backend authority
consumers can. Its exact API exposes only ticket/budget/binding/evidence mutations.
Compromise of that service role is still a protected-record trust-root compromise.
If reviewers require IAM separation even against compromise of the state service,
this proposal is insufficient: separate authority and mutable-state storage with
cross-store revision/transaction design is a different decision and scope.

## Remaining gates and ownership

Parent reviews code and authorizes any deployment. The product remains not
callable. Route and IAM readback, exact single-role/Runtime-version isolation,
immutable artifacts, actual protected operator approval, and budget remain gates.
Peer owns input/session/log limits and network/cost configuration. The new service
fanout (three signed state operations on success, Runtime control reads on reserve
AND claim, multiple serializable state transactions, no automatic retry) must be
included in that peer's final all-service pricing/envelope. Existing cost approval
must not be reused without review of this changed architecture. This document
makes no pricing or network configuration change and confers no budget approval.

## Review posture

- Security: Runtime repository access removed in code; actual IAM isolation and
  AWS-authenticated route require deployment evidence. No authority writer API.
- Reliability: atomic holds/claim/completion; uncertainty retains funds and denies
  resend. No automatic recovery from orphan RESERVED/CLAIMED tickets.
- Performance: bounded operations; existing full-partition Query scale still needs
  an externally enforced bound. No new pagination or generic storage interface.
- Cost: no approved budget; peer must account for service fanout and lifecycle.
- Operational excellence: default off, separate route, restricted receipts;
  cloud authentication and IAM policy proof are NOT established by unit tests.
- Sustainability: one attempted Model send per capture; no resources created.

## Bounded offline verification

Existing repository virtualenv, no dependency install:

```sh
python -m pytest -q tests/test_diagnostic_exchange.py tests/test_diagnostic_capture.py tests/test_capture_ticket.py tests/test_capture_dispatch.py tests/test_capture_deadline_review.py tests/test_capture_review_regressions.py tests/test_foundation_admission.py tests/test_foundation_wiring.py tests/test_serverless_templates.py --disable-warnings --maxfail=3
```

Result: **235 passed, 4 skipped, 2 warnings**, 22.90 seconds. Four skips are
SQLite variants of explicitly DynamoDB-only CAS tests; all corresponding DynamoDB
variants ran. The suite exercises both SQLite and mocked DynamoDB service paths,
exact AWS event context and role/capture/manifest binding, forbidden operations
and fields, aggregate hold rollback, stale/concurrent claims, transaction conflicts
on reserve/claim/complete, evidence-write rollback, expiry/revocation/user changes,
actual SigV4 signing with synthetic credentials, lost claim/completion responses,
and timeout with zero retry. No network inference or real AWS authentication was
performed; these tests are not deployment/IAM proof.
