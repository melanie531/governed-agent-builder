# Catalog + Builder coherent code release

2026-09-12. Target: https://de32ssfw7gsad.cloudfront.net/ (unchanged).
This is a frontend/backend code update, not completion of the live MVP.

## Reviewed source mapping and minimal correction

Starting branch `feat/live-capabilities`, HEAD `20106af8278c944301bc738da26dfa28c9166613`.
Includes native Catalog `848c0f4`, Builder `707e598`, telemetry repair `868436e`.
Existing cloud package is expected to map to `49b67ba` plus only telemetry.py
from `868436e`; exact owned module byte mapping is a mandatory deployment gate.
Desired package uses this release's complete committed source, with frozen uv
lock, binary Linux ARM64 Python 3.13 dependencies and native SDK service models.

Reviewed backend changes versus cloud: `backend/app.py`, new
`backend/builder_catalog.py`, `backend/live_catalog.py`. Telemetry bytes must
remain exactly equal to the independently deployed fix. Auth is not redeployed.
Frontend changes: native Catalog descriptors/access/readiness and Builder draft
preservation, exact component pins, library separation, typed binding issues.
No source identifiers are converted into legacy fixture aliases.

Minimal release correction: fixture Catalog already labeled rows as synthetic,
but did not explicitly state native connection status. Add
`native_connection_state: NotConnected` in fixture API snapshots and the visible
`Native Registry / Model Gateway: NotConnected` warning. Preserve fixture rows and
Builder functionality; no new mode or feature flag, no automatic source approval.
Regression checks both readiness and retained Foundation choices.

## Fresh sequential verification

Before commit, using the exact release source working tree:
1. `uv run --locked --extra foundation-runtime pytest -q --ignore=tests/test_model_gate_faults.py`
   **999 passed, 3 warnings, 65.66s**. Includes native Catalog, Builder, auth,
   callback/verification, delivery and flush regression. Not historical evidence.
2. `cd frontend && npm run build`: TypeScript/Vite passed; existing bundle-size
   warning remains. Executed only after pytest to avoid dist/assets interference.
3. `cd frontend && npm run test:e2e`: **9 passed, 21.9s**. Includes fixture
   create/evaluate/revise/retest/export/approval, exact new Catalog/Builder draft
   round-trip, NotConnected warning, fail-closed Catalog and hosted entry/auth UI.
   Offline fixtures/mocked auth are local tests, NOT authenticated cloud proof.

Three untracked MODEL-GATE-FAULTS files excluded from tests, staging and package.
No parallel writer, nested worker, migration, user invitation, password change,
SSM secret read, seeded cloud run or live model/tool/evaluation call.

## Cloud boundaries and release gates

Fresh existing-profile STS matches existing Studio CFN/CloudFront/Cognito privately.
Business/Worker only, controlled RevisionId-guarded `update_function_code` with
one matching package. No CFN update: native discovery configuration is absent;
this slice has no source authority and needs no new BusinessRole discovery IAM.
Preserve all function environment/configuration, attached/inline role policy
fingerprints, resource physical IDs, templates, parameters, outputs, Cognito,
DDB, SSM and public studio configuration. Auth and other functions untouched.

Before mutation: private versioned code/frontend rollback artifacts and resource
fingerprints, read-back hash checks, fresh no-drift gate. Assets first, index last;
private existing S3 origin, no old-asset deletion, no API cache modification.
After mutation: actual Lambda CodeSha256 and downloaded owned-module bytes;
S3 plus actual CloudFront index/JS/CSS/status byte equality; preservation checks.
Direct code release intentionally leaves CFN artifact parameters unchanged. A
future CFN release must explicitly preserve this newer artifact or revert code.

## Honest readiness

Current Catalog mode remains `fixture`, native Registry / Model Gateway is
**NotConnected**, producer/live flags remain OFF. Existing demo Builder remains
available and explicitly synthetic. Native Builder logic is shipped but not
live-enabled. No native foundation bindings, source authority or grants created.
Real Catalog requires rightful explicit owner source-authorization record,
exact source identities/exposure, separate IAM/transport review and bounded
read acceptance. Native execution bindings, real evaluation and full MVP remain
incomplete. Metadata approval must never be manufactured to pass these gates.

Authenticated cloud journey and AWS Console acceptance pending actual browser
inspection. Existing authorized profile only; absent session means unverified,
not passed. API verification is separate from Console/UI evidence.

Well-Architected: Security first, authority/configuration preserved; Reliability
sequential regression, versioned rollback and byte readback; Performance live
unmeasured, bundle warning retained; Cost no paid workload calls (release API/
storage costs not measured); Operations exact source/package receipts and CFN
drift disclosed; Sustainability existing resources only, no new compute stack.

Cloud receipt and final acceptance will be appended after actual deployment.
Private evidence: ignored `artifacts/catalog-builder-release/`.
