# Provider model metadata: corrected source-only result

Baseline actually inspected: `ceac041e3955c2057191816bcc4cb073b6b311c8`, on
`feat/provider-model-metadata`. **The old Mantle-coupled source is not approved
for the Runtime-only review and must not be promoted as compliant.** This document
supersedes the earlier activation instructions, not the historical receipts.

## Patch scope

- `backend/provider_model_metadata.py`: replace mandatory Mantle route mapping with
  schema-v3 Runtime-only documentation policy, exact native ID, supported APIs/region,
  evidence hashes and scope-bound approvals. Explicitly reject legacy snapshots and
  route fields; no target call, qualified route ID, endpoint or credentials accepted.
- `backend/live_catalog.py`: no AgentCore client construction for this documentation
  provider; expose native documentation fields. General Mantle-capable ModelGateway
  implementation and shared catalog account-binding logic are unchanged.
- `backend/builder_catalog.py`: documentation is not a selectable model route, even
  with exact owner Foundation pins; old selections remain non-deployable.
- Replace scoped tests and the unapproved proposal; add `MODEL-RUNTIME-CONTRACT.md`
  with public API/SDK evidence and the precise binding gap. No runtime adapter added.

## Evidence and approvals

The proposal retains the prior normalized GetFoundationModel result and its original
request ID/timestamps, plus the prior model-card hash. It adds a hash of that native
projection and a new source digest. No metadata refresh took place. Source and
exposure approvals remain **false**. This is a new documentation review candidate,
not a migrated/approved Mantle route. Native identity, metadata visibility, Foundation
composition approval, account entitlement and verified execution are separate gates.

Public docs confirm Runtime has a native Messages route as well as Converse/Invoke.
Gateway documents explicit provider routes and IAM outbound auth, but the actual
Runtime target/profile/version-header/signing binding has not been verified. Status
is **NOT_CONFIGURED**, not “unsupported globally” and not “ready after URL change”.
See `MODEL-RUNTIME-CONTRACT.md` for links and policy provenance limitations.

## Offline checks

- Existing `.venv/bin/python -m pytest tests/test_provider_model_metadata.py -q`:
  **22 passed** initially; six additional reapproval/client-construction cases were
  subsequently included in the final combined run below.
- Existing-dependency regression command:
  `.venv/bin/python -m pytest tests/test_provider_model_metadata.py tests/test_builder_catalog.py tests/test_live_catalog.py tests/test_model_catalog_metadata.py tests/test_native_catalog.py tests/test_native_catalog_connection.py tests/test_native_catalog_fixes.py -q`:
  **127 passed** in the final run (28 scoped provider cases), two existing
  Starlette/httpx/anyio deprecation warnings. `git diff --check` also passed.
- No denied peer AWS/environment wrapper executed or retried. No environment or
  credentials modified. No live AWS calls, new permissions/grants, paid inference,
  cloud writes, UI deployment, build, PR or merge. Offline passing is not hosted QA
  or authenticated route verification.

The four pre-existing untracked fault/release files were preserved and excluded.
`origin/main` is absent; outgoing commits are checked against the existing
`origin/feat/provider-model-metadata`, which matched the baseline at inspection.
Publication, if allowed, is ordinary non-force push of only the corrective commit.
