# Native Catalog release — 2026-09-12

Fresh read-only operator checks confirm the completed registry-only release of
`governed-agent-builder-serverless-app` in `us-west-2`, with stack status
`UPDATE_COMPLETE`; that completed deployment has no pending redeploy. The isolated worker could not reach AWS, but the parent independently completed
current AWS revalidation through the existing authorized operator path. Account,
region and existing Studio target matched privately. Do not rerun
`scripts/native_catalog_release.py`: it is non-idempotent and would overwrite
rollback evidence before encountering the existing discovery policy. Neither
setup.main nor release.main was run during finalization.

The project Registry is `governed_agent_builder_catalog`. Its currently verified native record
is `aws_knowledge_documentation`, type MCP, status APPROVED, version
`1.0.0-discovery.20260912`. Public source: https://knowledge-mcp.global.api.aws.
Server schema: `2025-12-11`; negotiated MCP protocol: `2025-03-26`; observed server
version: `1.0.0`. The five observed operations are:

- `aws___read_documentation`
- `aws___search_documentation`
- `aws___list_regions`
- `aws___get_regional_availability`
- `aws___retrieve_skill`

Source confidence is exact observed initialize/tools/list/native List/Get metadata
only. APPROVED admission is not an execution grant. Fresh native List/Batch and record Get checks establish discovery under operator
credentials; the approved version and observed descriptor digest match. The genuine
native SDK app verification used operator credentials and local authentication;
it does not prove hosted authenticated access or Business-role transport.
Authenticated hosted Catalog was not tested. QA login remains a separately
coordinated acceptance step. ModelGateway is `NotConnected`; tool bindings and
execution remain unverified. No model, provider, or tool calls were made here.

The generated private source configuration is embedded in the deployed ZIP as
`backend/native_catalog_source.json`, enabled by `NATIVE_CATALOG_PACKAGED_CONFIG=1`.
It is not checked into source. Exact account, source identities and ARNs remain in
ignored `artifacts/native-catalog/source-config-v2.json`, `record-final.json`, and
`registry-created.json`; these artifacts must never be committed. The setup
script's fixed Studio distribution/domain are routing identifiers, not credentials;
target binding must be assessed against private saved stack evidence before any
future authorized setup. Backend source bytes were left unchanged during this
finalization to preserve the release hashes.

The current Business ZIP download exactly matches the saved release and deployed
CodeSha256. Comparison confirms both backend source files match that release,
the embedded JSON equals the private source configuration, and `record_revision`
matches every exposure approval. Every original ZIP member outside the three
allowed replacements retains its bytes and metadata, including QA enrollment,
authentication, and telemetry. No enrollment content is published. Current CloudFormation template
comparison permits only Business and BusinessRole changes: NativeCatalogDiscovery
contains exact-registry ListDiscoverableRegistryRecords and exact-record
GetDiscoverableRegistryRecord; other role policies are unchanged. Current physical IDs and stack outputs match rollback. Current environment values
preserve all previous settings plus the two reviewed native Catalog flags.
Business ZIP SHA-256: `7d9498d199bf59157cbd326b0966f32bb46b962e222cce9be0da20a881c4d724`.
Embedded private configuration SHA-256: `81f7d0d4c4b3b087781b19f49fdf83e1f000829d0374f1fb1d8c40e18e3b351e`.

Final focused verification reran the following offline command: **195 passed**,
with two existing dependency deprecation warnings, in 9.17 seconds. No concurrent
fault suite or live/model integration suite was run. Despite its filename,
`test_live_catalog.py` exercises the local Catalog contract.

```sh
.venv/bin/python -m pytest -q tests/test_native_catalog.py tests/test_native_catalog_fixes.py tests/test_native_catalog_connection.py tests/test_live_catalog.py tests/test_builder_catalog.py tests/test_authorization.py tests/test_hosted_auth.py tests/test_qa_enrollment.py
```

Historical evidence: `tests.log` records 195 passes but omits its command; the
explicit command above reconstructs that focused selection and now has its own
receipt. `browser-tests.log` records five passing local-auth browser tests using
`playwright test e2e/builder-catalog.spec.ts e2e/journey.spec.ts e2e/zz-ai-catalog.spec.ts`.
Those browser tests and the prior production frontend build were not rerun here.
Fresh public readback confirms HTTP 401 without authentication and exact frontend
index matching; saved bundle evidence confirms source-state UI assets. Hosted acceptance is
not claimed. Private `final-verification.json` separates finalization checks from
historical receipts; `final-tests.log` contains the actual rerun.

Well-Architected status:

- **Security:** scoped discovery admission, preserved auth and enforcement; hosted user and Business-role acceptance outstanding.
- **Reliability:** current deployment complete; actual package preservation and public endpoint checks pass.
- **Operational excellence:** private rollback/readback receipts retained; release script must not be rerun after completion.
- **Performance efficiency:** bounded cached metadata and batch reads; no production load validation.
- **Cost optimization:** registry metadata only; no inference/tool calls or new cost measurement.
- **Sustainability:** bounded refresh and reuse; no measured utilization claim.

Rollback requires a separately reviewed non-replacing changeset based on ignored
`rollback-template.json`, `rollback-business-config.json`, and `Business-rollback.zip`;
restore the previous frontend index S3 version. Preserve existing physical IDs,
outputs, Cognito identities, QA enrollment and telemetry. Do not delete state or
rerun the completed release script. No AWS writes were performed here.

## Source publication and locked review

The target repository was verified private through the authorized parent Git path.
The current remote feature branch matched baseline `9a342d8` before publication;
there is no local `origin/main`, so this feature branch's actual remote ancestry
is the comparison point. Only the eight native Catalog task files belong in this
release commit; concurrent model-gate fault files are excluded. No PR, merge,
force push, resource recreation or redeploy is part of finalization. The parent
publishes normally and verifies the remote SHA equals the resulting commit;
use that fixed SHA for peer review, not a moving branch name.

Peer acceptance must sign in to the existing hosted Studio with the already
provisioned QA account, refresh Catalog in the authorized workspace, and record
`Registry: connected` with the genuine record and five metadata tools. Do not use
forged Lambda events or operator SDK results as Business-role proof. No passwords
were read. This acceptance remains outstanding and does not block source review.
