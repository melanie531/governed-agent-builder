# Verification report — 11 September 2026

## Executed locally

| Check | Result |
|---|---|
| `uv run --locked pytest -q` | **92 passed** in 1.66 seconds |
| `npm --prefix frontend run build` | **Passed**: TypeScript and Vite production build |
| `npm --prefix frontend run test:e2e` | **2 complete browser journeys passed**, 9.5 seconds, installed Google Chrome, actual API + async worker |
| Dependency install audit | npm reported **0 vulnerabilities** |
| Portable ZIP runner | Backend test extracted and executed `python run.py`; case evidence and measured score matched the job result |
| Live local root + assets | HTTP 200 at `127.0.0.1:5187`; JavaScript and CSS served with correct types |
| Live bootstrap API | HTTP 200, three synthetic personas, `LOCAL SIMULATION` |
| Server bind | Verified listening only on `127.0.0.1:5187` |
| Portal proxy | Existing operator portal at port 59374 returned **401 to an unauthenticated HTTP probe**; portal session authentication is required and was not bypassed |
| Managed browser tool | Direct local navigation blocked by browser policy. The permitted installed-Playwright path supplied browser test evidence; no policy settings were changed |

The private repository was created and the initial implementation pushed to `feat/local-first`. Final commit and remote SHA are reported in the delivery message rather than embedded here (avoids a self-referential commit hash).

## Browser acceptance coverage

The browser tests use real Cloudscape controls, not direct API shortcuts for business flow:

- DEV ONLY persona entry and official Cloudscape console rendering;
- four-step Wizard, foundation radio selection, filtered model Select and tool/skill Multiselect;
- user-written prompt, rubric, JSON file upload and editable dataset;
- asynchronous Deploy & Test, actual output and measured check evidence;
- passing local gate → local invocation;
- recorded execution trace and backend ZIP download;
- immutable revision with deliberately failing required term → blocked invocation;
- repaired data but required unconfigured LLM judge → Needs changes despite deterministic success;
- fourth revision restores local deterministic profile → Local checks passed;
- capability request → Admin decision with reason → business user sees approval and newly available existing seeded tool;
- Admin revokes the original knowledge-tool grant → previously passing agent's backend invocation is denied;
- second identity cannot see first identity's agents and sees only its permitted Claude route;
- changing foundation clears model/dependent selections;
- 390 × 844 responsive viewport: no horizontal document overflow;
- no browser `pageerror` events during the full primary journey.

## Backend acceptance coverage

92 tests include every protected route requiring a session; business/admin role matrix; IDOR for agent/read/revision/deploy/job/export/invoke; canonical identity and ignored forged role headers; denied extra identity payload fields; unauthorized/wrong-kind/unknown/incompatible/version-forged components; catalog intersection; session expiry and rotation; CSRF, origin, cross-site and DNS rebinding checks; request-size cap; local invocation and retry revocation; worker-time revalidation; real measured pass/fail/citation/refusal/format/missing-judge gates; immutable version/digest and stale results; idempotency; policy changes; revision during execution; durable deadlines; restart/resume; two unrelated async agents with independently bound results; queue capacity; grant persistence without reseeding; admin approval/rejection; external data-policy denial; export reproducibility; strict safe dataset schema; audit exclusion of prompts; fail-closed AWS/production mode; offline AgentCore runtime SDK operation-shape checks.

## Visual artifacts (local only, ignored by git)

- `artifacts/cloudscape-my-agents.png`
- `artifacts/cloudscape-wizard-evaluation.png`
- `artifacts/cloudscape-results-pass.png`
- `artifacts/cloudscape-missing-judge.png`
- `artifacts/cloudscape-admin-approval.png`
- `artifacts/cloudscape-revocation-blocked.png`
- `artifacts/cloudscape-mobile.png`
- `artifacts/browser-export-v1.zip`
- `artifacts/playwright-report/`

All screenshots show synthetic data only. The executing model could capture images but could not view image content. **Pixel-level visual review remains for the parent/operator**; browser accessibility-tree interactions, responsive overflow checks and UI rendering passed. No screenshot was published to GitHub.

## Known limitations, not hidden passes

- Two dependency deprecation warnings remain in the Starlette/httpx TestClient stack; tests pass.
- Vite warns about a large first-version Cloudscape bundle (~1.19 MB JS and ~1.24 MB CSS uncompressed). Route-level splitting and measured performance optimization are future work, not a release claim.
- Production Okta, AWS Runtime deployment/readiness/version binding, both live Gateways, Registry discovery, real model routing, AgentCore Evaluations, CloudWatch and Splunk are not integrated.
- AWS adapter tests are **offline only**. No cloud resource or paid model invocation was authorized or executed.
- The local worker supports one process, two concurrent fixture executions, durable replay without external side effects. It does not implement a distributed AWS job lease.
- A local persona selector inherently lets the operator become the demo Admin. This is not multi-user production authentication.
- The authorized OpenClaw Portals interaction itself has not been independently verified from the operator's authenticated portal session. The app is serving and its direct local browser/API journey is verified.
