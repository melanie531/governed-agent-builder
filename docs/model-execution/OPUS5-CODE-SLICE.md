# Opus 5 Studio registration slice

Code only. No cloud writes, inference, deployment, grant changes, PR or merge.

## Base and scope

Base `82e65a81d8f04dbe0bc2141f3c80493637271ec5` is the parent's deployed integration revision. Fresh remote inspection found no `main`; remote HEAD is older `feat/local-first`. Latest integration at inspection, `8377bb99d849272eadf5198425cfc3a222a23705`, differs from this base only by peer-owned cloud-review documents. Branching from the deployed revision avoids carrying those documents as task-owned changes. The original checkout's four untracked parallel files were untouched.

Captured `business-DEPLOYED.zip` from the supplied recovery report has Runtime changes in `foundation_harness/{config,model_client,transport}.py` absent from the deployed Git revision. The backend Runtime catalog already matches that capture, while Git's later discovery/catalog work must be preserved. No Worker ZIP was available in the supplied evidence. This patch is source integration, NOT a replacement deployment ZIP; peer must reconcile actual current Business AND Worker bytes before deployment.

The parent reassigned harness ownership after implementation had begun. Harness changes and offline transport tests were preserved separately for the peer, not committed in this backend slice. The peer must implement the model fields/serialization in `opus5-product-contract.json`. No harness, IaC or deployed configuration is part of this commit.

## Delivered backend behavior

- Uses the same server-owned catalog config loader for catalog and Foundation registration. Neither ordinary users nor registration payloads select an unapproved URL/model mapping.
- Runtime registration resolves one exact `model:<gateway>:<target>:<profile>` binding and reads that target directly. No old `/inference/v1/messages` assumption or single-target restriction for Runtime. Tools and legacy model validation remain separate.
- Checks READY, AWS_IAM, ENFORCE, no interceptors, exact Runtime endpoint/signing/headers, target identity, and the existing `route_revision` approval. This digest covers target schema/credentials/headers, gateway role/engine configuration and exact model/response pin.
- Model version and targetDigest both equal that existing route revision. Registration's protected platform record includes the exact normalized model; self-service admission rechecks it against the selected immutable definition and current source/catalog/policy.
- This first slice deliberately uses an **Opus-specific reviewed Foundation source**, not a generic multi-model resolver. A selection cannot replace the source's model. Register a separate approved Foundation entry/source to retain an existing Haiku source unchanged.
- Empty Opus response identities plus null evidence is an explicit **unverified** configuration. It may be registered for review, but self-service/M0 admission fails `UNVERIFIED_RESPONSE_IDENTITY` before creating an approval. The peer harness must also reject it before dispatch. A syntactically valid evidence hash is an operator assertion, not an evidence-verification service; the operator must review real captured evidence before pinning it.
- Existing Haiku bindings and version/hash serialization remain unchanged. New Opus evidence/contract keys alter only Opus's approval revision. Native discovery remains non-executable; no readiness flags become true.

## Remaining pipeline work (not concealed by the tests)

1. Integrate peer harness support for runtime-passthrough/requestModel/responseModels, exact `opus5-text-v1`, optional `responseIdentityEvidence`, legacy hash-preserving serializer, string-only Engine first message, explicit thinking disabled, exact 256, one-call/no-tools, exact response comparison, no fallback/retry and reservation checks. This backend branch alone does not implement that transport.
2. Actual response identity remains unobserved. Do not fill `response_models` from the native model card or model self-description. Parent/peer needs an independently authorized evidence path before enabling product admission. This branch intentionally cannot bootstrap evidence by allowing an unpinned product call.
3. Route revision includes policy engine configuration, **not the contents/versions of all Cedar policies**. Independently retain/review exact policy-version evidence and effective IAM, and invalidate approval/epoch for policy changes as required. No claim of automatic Cedar policy-body drift detection.
4. Protected component/library records, native bindings, source registration and policy approval must agree. No actual records or grants created. Existing catalog `execution_ready`/`integration_ready` remains false. The code does not relabel a discovered model as granted or ready.
5. `platform_metadata` still uses verified project CFN FoundationRole. `FoundationJobs` rejects reuse across immutable packages. First package needs an unbound exact role; per-package provisioning/resolution beyond that is still missing.
6. `configured_jobs` needs protected `foundation-deployment` account/region/roles/bucket/network, plus producer_role and evaluation settings. Producer requires VPC, versioned private bucket, approved bundle/source match and actual Linux proof. Existing finalization/admission/Linux guards were not loosened.
7. Required records remain `foundation-source:*`, `foundation-policy:*`, `foundation-bundle:*`, `foundation-base-linux:*`, `foundation-approved:*`, `foundation-artifact:*`, `foundation-linux:*` (or existing verified composition evidence). Harness changes change executable source digest; regenerate approvals/bundles against final integrated code, never rewrite an immutable artifact.
8. Worker/Business packaging, 256-token schema/IAM/Cedar review, cost envelope and exact role grants remain peer/parent work. The earlier 16-token policy cannot authorize this slice. No live/producer flags changed. Keep Foundation direct-provider Denies and cumulative USD5/all-service reservation guards.
9. Full ordinary login → save → admission → owned Runtime → Gateway → result/usage remains **unproven**. Offline tests are not Linux, cloud or workload-origin inference evidence.

## Well-Architected status

Security: fail-closed pins retained; live IAM/Cedar and response evidence pending. Reliability: immutable definitions, CAS, artifacts and reservation guards preserved; producer/deployment pending. Operational excellence: isolated scoped commit and explicit config handoff; deployed-byte reconciliation pending. Performance: no latency claim; current transport's existing 10-second HTTP read timeout needs peer review within the 60-second total bound. Cost: no inference spend, reviewed all-service envelope still required. Sustainability: reuses existing native Gateway path, no new infrastructure/framework.

## Verification ledger

Interpreter used: `/Users/peiyaoli/Documents/projects/governed-agent-builder/.venv/bin/python` (existing Python 3.13 environment, no dependencies installed). Commands below use `PY` to denote that exact interpreter; it is not a secret or a new environment requirement.

| Context | Exact command following `PY` | Result |
|---|---|---|
| Combined isolated worktree, before ownership split | `-m pytest -q tests/test_foundation_executor.py tests/test_foundation_approval.py tests/test_self_service_admission.py tests/test_runtime_discovery_coexistence.py` | exit 0; 67 passed |
| Combined isolated worktree, before ownership split | `-m pytest -q tests/test_opus5_product_execution.py tests/test_runtime_pinned_model.py tests/test_runtime_pinned_catalog.py` | exit 0; 65 passed (at that revision) |
| Added end-to-end offline test, intermediate | `-m pytest -q tests/test_opus5_product_execution.py` | exit 1; 36 passed, 2 failed because test queried nonexistent `foundation_jobs` table. Corrected backend test to real `jobs` table; no product guard relaxed. |
| Combined local backend + preserved uncommitted harness | `-m pytest -q tests/test_opus5_studio_registration.py tests/test_runtime_pinned_catalog.py tests/test_foundation_approval.py tests/test_self_service_admission.py tests/test_builder_explicit_deploy.py tests/test_package_admission_contract.py tests/test_foundation_producer.py tests/test_foundation_finalization.py tests/test_runtime_discovery_coexistence.py` | exit 0; 160 passed |
| Exact backend-only snapshot with base harness, not uncommitted adapter | `-m pytest -q tests/test_foundation_approval.py tests/test_self_service_admission.py tests/test_runtime_pinned_catalog.py tests/test_runtime_discovery_coexistence.py` | exit 0; 58 passed |
| Exact backend-only snapshot with base harness | `-m pytest -q tests/test_opus5_studio_registration.py` | exit 1; 11 failed: baseline Model lacks Runtime route/new fields. Explicit peer adapter dependency, not claimed green. |
| Backend syntax | `-m compileall -q backend/foundation_approval.py backend/self_service_admission.py backend/live_catalog.py backend/runtime_model_catalog.py` | exit 0 |
| Machine-readable contract | `python3 -m json.tool docs/model-execution/opus5-product-contract.json` | exit 0 |
| Whitespace | `git diff --check` | exit 0 |

No whole-suite run, frontend rebuild, paid inference, cloud test, deployment or Linux execution. Existing Starlette/httpx deprecation warnings only in passing suites. The combined 160-pass run does **not** describe this backend-only commit in isolation; its 11 Opus tests deliberately remain red until peer harness integration.
