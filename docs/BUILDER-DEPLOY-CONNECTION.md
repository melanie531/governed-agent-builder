# Builder explicit deployment connection

2026-09-12. Source-only change from `510b0ae4c17eba31abe0e25e9e0208405d0b3ba5` on `feat/builder-explicit-deploy`. No AWS mutations, paid invocations, IAM/model interceptor/inference Deny/user-pool/data/configuration changes. SDK responses and artifact evidence in tests are synthetic, NOT cloud proof.

## Reproduced and repaired

The complete-binding counterexample initially failed `test_complete_binding_explicit_deploy_reaches_sdk`: its only readiness issue was the unconditional `runtime_not_connected`, despite verified synthetic Foundation admission/artifact and a configured deployment adapter. Removing that issue alone was insufficient: API and worker `validate_current` also rejected every live version.

- `backend/builder_catalog.py:assess`: exact native ID/version/source binding, current visibility/grants/data boundary and execution readiness checks remain. Compose these with actual deployment issues; no unconditional rejection.
- `backend/foundation_jobs.py:FoundationJobs.readiness_issues`: read-only server-state resolution through existing `approve_request`, `check_current`, `finalized_artifact`, role allowlist and private network checks. No authority renewal, artifact upload, reservation, Runtime creation or invocation on save/read. Fixed codes for missing saved version, deployment driver, verified artifact, approval, authority, source, role/network and readback support. The existing worker still verifies artifact bytes and target identity before create, then exact Runtime and DEFAULT endpoint versions before execution.
- `backend/app.py:live_readiness`, `validate_current`, `deploy`, `step_job`: GET detail recomputes readiness. POST `/api/agents/{id}/deploy` requires explicit `execution_mode: live`, current owned version and idempotency key. Existing `/deploy-test` remains compatible. Live Builder enqueue consumes current authority without silently renewing it. Both API and worker validate the live catalog before allowing FoundationJobs. Live definitions cannot enter the fixture runner or local invoke.
- `frontend/src/main.tsx:saveAndTest`: live Save draft persists only; separate saved-detail `retest` action POSTs `/deploy` with version/mode/idempotency key only. Ready and blocked states are distinct. Catalog five categories, MCP details, prompt/rubric/dataset drafts and immutable history are retained.

Call path: Save -> immutable definition; GET saved detail -> backend readiness; explicit Deploy -> authorized job + reservation -> durable worker -> FoundationJobs VALIDATING -> artifact readback + target verification -> FoundationDeployment.submit_new -> RuntimeDeploymentAdapter.submit/CreateAgentRuntime -> WAIT_RUNTIME -> exact version/manifest READY readback -> DEFAULT endpoint liveVersion/targetVersion checks -> existing bounded execution/evidence gates. Deployment eligibility is NOT release approval.

## Verification

- Initial counterexample: **FAILED** with unconditional runtime_not_connected (recorded before implementation).
- After implementation: **193 passed**, two existing warnings, targeted offline Python suite covering Builder, Foundation wiring/finalization/admission, Runtime adapter, authorization and Catalog UX/native connection.
- New negative cases: revoked grants, wrong owner/workspace, old version, missing artifact, missing model execution, missing tool execution, unverified execution binding even with ready flags, stale native source, disabled driver, wrong role/network, mismatched endpoint and Runtime versions; post-enqueue model revocation blocks worker.
- Save/revision: no jobs, no SDK create or invocation; history preserved. User role/endpoint/ready fields rejected by strict schemas. Complete live bindings still cannot request fixture fallback.
- Frontend `npm run build`: passed; existing large-bundle warning only.
- Browser tests: **3 passed** (10.0s): preserved native Catalog/Builder prompt and evaluation round trip; saved-ready version explicit Deploy sends only version/mode/idempotency key; five resource categories and declared MCP operations/back navigation. All browser API fixtures are explicitly offline.
- One initial parallel test/build run hit transient missing dist/assets while Vite replaced build output. Serial rerun after build: all 192 tests passed; final additional unverified-binding test brought the suite to 193 passing. No application workaround added.

## Actual remaining binding requirements

No cloud state was queried or changed in this slice. Existing native discovery advertises unverified execution, so deployed availability must not be inferred from offline success. Platform configuration must supply exact native component records also registered in the server-owned Foundation source, approved native ID/version/source revisions, current grants and domain policy admission for the immutable definition, dedicated reviewed workload role/exchange binding, model/tool execution integration, private versioned package with validated source/admission/deployment provenance and Linux/composition evidence, reviewed all-service cost envelope, exact target/VPC deployment policy, artifact reader and enabled durable live worker. Evaluation evidence remains independently required for LIVE_PASS; a created READY Runtime alone is not acceptance. Missing requirements stay blocked and are returned as readiness issues. This change does not create or approve any of those bindings.

## Repository handoff

Only scoped source/test/document files are staged. Excluded untouched files: `docs/MODEL-GATE-FAULTS.md`, `scripts/model_gate_faults.py`, `tests/test_model_gate_faults.py`.

Normal `git switch -c feat/builder-explicit-deploy` succeeded, no .git permission workaround. `git fetch origin main` returned `fatal: couldn't find remote ref main`; `git log origin/main..HEAD --oneline` returned `fatal: ambiguous argument 'origin/main..HEAD': unknown revision or path not in the working tree.` Remote symbolic HEAD is `refs/heads/feat/local-first`. Therefore new work is audited against the explicitly requested starting SHA and pushed only to the new feature branch, without force/PR/merge. The final commit SHA is supplied in the handoff response (not embedded recursively in this commit).

Security-first / Well-Architected: Security preserves strict inputs, ownership, least-privilege role checks and fail-closed authority; Reliability preserves immutable/idempotent jobs and per-stage revalidation; Operational excellence adds concrete readiness failures and reproducible tests; Performance adds read-only validation, not network calls on save; Cost retains the existing reservation/call caps with no paid tests; Sustainability reuses the existing deployment lifecycle instead of adding infrastructure.
