# Agent Studio home recovery and model display — review only

Target repository: melanie531/governed-agent-builder.
Branch: fix/studio-home-and-model-clean.
Baseline: 6fcb2fae7e3c8014c8e0fa4824b1d2ca2c31766d.
No deployment, PR or merge. This report and tests are committed with the implementation.

## Changes
- Session-check 403 remains an error/denied state, never anonymous and never automatically redirects. 401 retains anonymous semantics; service/network failures retain error semantics.
- Error state now preserves the existing public introduction and three feature sections. Explicit Sign in again and Retry session check exist in both page and account menu. Re-login uses existing /auth/login; no client cookie manipulation or authorization changes.
- AI Catalog displays Anthropic / Claude Haiku 4.5 only for the exact recognized native model ID (with recognized region/profile prefix). Unknown models retain source names. Technical identifiers are collapsed inside View details. Existing binding IDs, permissions, approval and readiness are unchanged.
- Capability table no longer hardcodes the provider to Bedrock. No backend or infrastructure source changed.

## Actual execution
Build: `npm run build` passed (existing large-bundle warning).
Final full local browser run: **30 passed (50.1s)**.
Command: `PLAYWRIGHT_CHANNEL=chromium npx playwright test -c parent-recovery.config.ts --workers=1 --reporter=line`.
The temporary runner uses a fresh local fixture database and port 5191, because another checkout was using the standard hardcoded 5188. It imports this checkout's backend.app.create_app and serves this checkout's built frontend. Standard reproduction without the port collision: `cd frontend && npm run build && PLAYWRIGHT_CHANNEL=chromium npx playwright test --workers=1`.

Eight public-home recovery cases cover permission-detail 403, gateway 403, unknown JSON 403, HTML 403, invalid JSON 403, 500, 503 and network abort. Every case checks public content, retry still failing, no protected workspace, no automatic login, menu recovery actions, and explicit /auth/login navigation.
Additional cases cover anonymous 401, mid-session 401 versus 403, business denial, hosted no-role-switch, pending verification, Models display/unknown IDs/readiness, draft retention, approval/revocation and local journeys.

## RED evidence and failure correction
Before the public-home fix, the permission-403 case failed because `Your expertise. Your workspace.` was absent; seven cases did not run due to max-failures=1.
After the implementation, focused run: 18 passed / 1 failed. The remaining locator expected raw Forbidden; the rendered Cloudscape Flashbar accessible group actually read `Action blocked Access is restricted. Request access or contact your administrator.`. The test now asserts that exact group and still asserts the same signed-in identity, absence of anonymous entry and no redirect. No permission or identity assertion was removed.
The 6 pass / 6 fail result mentioned elsewhere is not a result produced by this checkout and is not claimed here.

## Existing test alignment included explicitly
Four existing browser specs had already been updated in local test-only commit 1777d50 to match the v5 baseline's labels. Their file changes are included explicitly in this single branch commit (that unrelated local commit is not an ancestor). Reviewed changes are labels/selectors such as Add to draft, Pending approval, Sample test passed and Run sample. Model option selection, component IDs, request payloads, approval/revocation checks, grant checks and draft retention assertions remain. No model binding ID or authority input was rewritten.

## Evidence limits
All 30 browser tests are LOCAL fixture/mocked-HTTP tests, not production Cognito or production authorization verification. In particular, mocked /auth/login proves user-triggered navigation, not successful live authentication. This revision has NOT been deployed or validated on the live website. Model presentation mapping is supported by the exact native-ID contract; current deployed metadata has not been independently re-read for this review. Live Models and error-state recovery must be independently verified after approved deployment.
