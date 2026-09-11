# Bounded first-target packaging smoke, 2026-09-12

## Result: infrastructure READY, application import NOT proven

One actual AgentCore Runtime was created in the identity-matched current Studio
account using the exact base ZIP. The signed invocation failed at endpoint
routing, before application execution: the tool incorrectly supplied numeric
runtime version `1` as the invocation qualifier. AWS requires an **endpoint
name**, not a version. This is a smoke-tool error, not evidence of a package
import error or permission denial. No Linux application acceptance is claimed.

No second runtime was created. The one-runtime budget was respected. No package
bytes were changed. The numeric qualifier defect is corrected in the dedicated
script to use `DEFAULT` after checking its `liveVersion`, but that correction
has NOT been executed on the target platform. Do not relabel this run PASS.

## Exact binding and cloud evidence

- Source HEAD at execution: `2db87d806452f40a616273dc63ae4b25620af37d`.
- Foundation source digest: `e4a8b11581124d34b4f041703f0c5f4585c3c5b959cbcadc34f84d6bce249446`.
- ZIP: `artifacts/foundation-admission/runtime-base-m15.zip`.
- ZIP SHA-256: `0fcae9df5f38cb3aa4b57dfd9ca6320e6fbe892b97f7a919e20a3524b6951e82`.
- Exact packaged source bytes matched the repository. Base status explicitly
  has no admission configuration and `deploy_ready: false`. All native shared
  objects have Linux ELF AArch64 machine ID 183. This static check is NOT import proof.
- Existing private project release bucket:
  `governed-agent-builder-serverless-artifac-releases-9cof97su8q8f`.
- Object key: `foundation-import-smoke/0fcae9df5f38cb3aa4b57dfd9ca6320e6fbe892b97f7a919e20a3524b6951e82/runtime-base.zip`.
- S3 VersionId: `pYJH.HyPexgKGeXwDQ0AYwgzfEBUYkfH`.
- Downloaded version SHA-256 matched the exact ZIP; object is retained.
- Runtime: `gab_foundation_import_smoke-g01EcRHv0o`; version `1`.
- Observed status: `READY`; code configuration `PYTHON_3_13`, entry `main.py`.
- Network: explicitly authorized `PUBLIC` startup smoke, not a fallback for
  account denial and not proof of private customer egress.
- Lifecycle: idle session timeout 60 seconds, max lifetime 120 seconds.
- Calls: one CreateAgentRuntime; one InvokeAgentRuntime attempt, payload only
  `{"run_ref":"synthetic-smoke"}`. No application response received.
- Exact service error code: `ResourceNotFoundException`.
- Sanitized diagnostic: `No endpoint or agent found with qualifier '1'`.
- StopRuntimeSession encountered the same invalid qualifier. DeleteAgentRuntime
  succeeded; GetAgentRuntime returned ResourceNotFoundException and a subsequent
  list confirmed no remaining named smoke runtime. Cleanup: **DELETED_VERIFIED**.
- Smoke process duration, including identity checks and cleanup: **60.20 seconds**.
- Local sanitized receipt: `artifacts/foundation-admission/import-smoke-proof.json`
  (ignored artifact; not delivered by git).

Expected but NOT observed application response:

```json
{"status":"BLOCKED","code":"AUTHENTICATED_BACKEND_REDEMPTION_NOT_CONNECTED","production_ready":false}
```

## Security, scope and cost

STS matched the existing Studio CloudFormation stacks, CloudFront distribution
and Cognito pool before writes. Reused FoundationRole unchanged, including its
AgentCore-only service trust with `gab_foundation_*` SourceArn restriction,
exact exchange grant, direct Lambda deny and model restrictions. ModelRole's
CreateInference deny remained present. Zero IAM, VPC, BPA, user or existing
runtime modifications. No authority producer or admission JSON was added.

The base entry's missing-admission path returns before constructing a boto3
Session/client or reaching Gateway, but this run did not reach that path.
No model, tool, Browser or evaluation dispatch was requested. Actual provider
usage and actual billed cost are **unknown**, not zero. Engineering variable
estimate remains below USD 1, not a hard invoice cap; runtime time may be
billable and retained private S3 storage is chargeable. No smoke runtime remains.

Not proven: Linux startup/import, missing-admission response, workload admission,
Gateway, model, Browser, evaluation, UI, private egress, full dependency coverage
or production readiness. CloudWatch logs were not scraped: this was an endpoint
routing failure, not an observed application startup exception.

## Tool behavior and verification

`scripts/foundation_import_smoke.py` requires explicit expected account, fixed
approved profile/region, exact source HEAD/hash and a fresh receipt. It rejects
an existing same-name probe for receipt reconciliation rather than creating a
duplicate; it does not implement automatic adoption. It performs no IAM edits.
A subsequent authorized run must independently check the corrected endpoint
binding; this document does not authorize another runtime.

Checks executed: exact ZIP/source/ELF validation passed; incorrect artifact hash
was rejected. Existing package/admission/IAM regression tests: **40 passed**
(two pre-existing dependency deprecation warnings). Updated script compiles;
`git diff --check` passes. No target retry or package rebuild was performed.

Publication ancestry check: this repository has no `origin/main` (confirmed by
remote heads query). Before the smoke commit, HEAD exactly matched
`origin/feat/live-capabilities`; no unpublished peer commits were present.
