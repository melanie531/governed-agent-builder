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

## Completed deployment and authenticated acceptance

Deployed implementation SHA: `21616c1e90058ac113590f49e52dddf70fa483b2` on `feat/live-capabilities`.
Business ZIP SHA-256: `45248dc5a71b3cd828adf1b190885b72adea7e68957e8ebaee44a747d315bc45`.
Business app.py SHA-256: `41125aa5713c834f50293f2a95aef8617e3a744e125a41ccbec46063c3d6d591`.
Release reached `DEPLOYED_HASH_VERIFIED`: actual Lambda download and all S3/CDN
frontend bytes match. Other package members and configuration fingerprints match
fresh rollback. Existing native configuration, QA enrollments and telemetry remain.
Final combined focused backend tests: **197 passed**, 2 existing warnings (7.66s).

Real browser acceptance used actual Cognito form and approved business QA SSM
credentials only in process. No cookie injection, mocked API, storageState or trace.
Initial automation used a button locator for a link and timed out before credential
entry; corrected to the observed real link and reran the complete acceptance.
- Authenticated My agents loaded. Native `/api/catalog` returned live mode.
- Exact five tabs; no Tools tab. Registry connected; ModelGateway NotConnected.
- One top-level server; five children matching its stable parent_id.
- Actual names: aws___read_documentation, aws___search_documentation,
  aws___list_regions, aws___get_regional_availability, aws___retrieve_skill.
- Models empty/NotConnected, server count 5, all operation names visible in detail.
- Create foundation -> prompt/evaluation -> Catalog -> operation -> same draft
  retained exact operation ID/version, prompt, criteria and dataset. Save draft
  remained available; no automatic deployment. QA draft was not persisted.
- No model/tool/evaluation invoked and no grants or user data changed.

Private screenshots: `artifacts/catalog-ux/live-models.png`, `live-mcp-list.png`,
`live-mcp-detail.png`, `live-draft.png`. DOM/network evidence: `qa-result.json`.
These are actual authenticated captures. This model cannot view image content;
independent visual layout review is explicitly pending with the parent/peer.

Implementation source and deployment are complete, not a claim that live execution
or the full MVP is complete. The documentation-only follow-up commit does not
change the fixed deployed implementation SHA above.
