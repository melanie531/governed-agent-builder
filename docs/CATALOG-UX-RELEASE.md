# Catalog UX correction

2026-09-12. Existing Studio: https://de32ssfw7gsad.cloudfront.net/.
Baseline verified: `a51dd2cee7490c1c714d2acd869ccbeaf1fe8512`; remote matched.
Only the three pre-existing untracked model-gate fault files were present; excluded.

## Scope

Business Catalog has exactly Models / MCP servers / Skills / Agents / Other.
No standalone Tools tab or top-level child operation. Actual Cloudscape Table,
Tabs, Modal and ExpandableSection; no new custom layout or console branding.
Server detail groups operation IDs by exact `parent_id`, never name parsing.
Per-operation requests and draft selections keep original IDs and versions.
Unsupported/custom descriptors are not promoted to MCP; skills remain skills.
The main page has compact source statuses, Connection details and one execution
notice. Technical IDs, revisions, schemas and policy information are expandable.
Admin Tools & skills governance and the Builder workflow remain unchanged.

Backend `/catalog` retains internal child entries for existing consumers but
counts only top-level resources. Hidden parents prevent child list/detail/request
exposure even with an independently permissive child scope. Source filters remain.

## Verification before release

- Existing focused backend regression: 195 passed (8.91s).
- New offline UX tests: 2 passed; exact five public observed operation schemas,
  stable parent identity/count, hidden parent and cross-workspace denial.
- TypeScript/Vite build passed. Existing bundle-size warning remains.
- Playwright: 10 passed (21.6s), including exact five categories, no Tools,
  empty models, MCP detail, operation-to-draft pin, prompt/evaluation preservation,
  back navigation, fixture approval and existing create/evaluate/revise journey.
- Offline fixtures and mocked authentication are NOT authenticated live evidence.

## Scoped release procedure and recovery

Fresh private target verification compares STS to Studio CFN, CloudFront and
Cognito. Original Business ZIP and frontend objects plus checksums captured under
ignored `artifacts/catalog-ux/`; existing owned Python source equals baseline.
Publish source commit to the existing private feature branch before cloud writes.
Patch only `backend/app.py` in the downloaded original Business ZIP, preserving
all other ZIP members and metadata, including QA enrollment, native source JSON,
authentication, telemetry and SDK. Do not rebuild a package from source.
No Worker/Auth/Authorizer, CFN, configuration, IAM, gateway, Registry or state writes.
Deploy existing private S3 assets first/index last and invalidate CloudFront.
Verify CDN/S3 bytes, Lambda package digest, unchanged configuration/role policy
fingerprints, resource identities, other function hashes and studio configuration.
Direct code update leaves CFN code artifact drift; a later CFN release must retain
this code version rather than revert it accidentally.

Release state is durable in ignored `artifacts/catalog-ux/release.json` and is not
completion until DEPLOYED_HASH_VERIFIED. If interrupted, read it before resuming;
never overwrite rollback evidence. Rollback is the captured Business ZIP plus
previous frontend index/assets; retain current private enrollment/source state.

Authenticated QA acceptance follows deployment via approved QA SSM values held
only in a process and entered into the actual Cognito form. No credentials in
arguments/logs/traces/storageState. Browser evidence must be recorded separately;
independent peer visual review is not implied by automated DOM checks.

## Remaining capability status

Native Registry metadata is connected; ModelGateway is NotConnected. Live model,
tool execution and evaluation are not enabled by this release. No MVP claim.

Well-Architected: Security preserves identity, least privilege and private origin;
Reliability has rollback and hash verification; Operations has fixed source and
scoped receipts; Performance retains the bundle-size warning, unmeasured live
load; Cost has no inference/tool calls; Sustainability reuses existing resources.
