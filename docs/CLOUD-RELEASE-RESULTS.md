# Cloud release: result/evaluation checkpoint

2026-09-12. **DEPLOYED; fixture-only, NOT live AgentCore acceptance.**

Studio: https://de32ssfw7gsad.cloudfront.net/
Public readiness: https://de32ssfw7gsad.cloudfront.net/release-status.html

The earlier failure to deploy left the user seeing the old cloud implementation.
This release corrects that deployment gap, not the unfinished live platform.
The public landing/sign-in design remains intentionally unchanged. New Report,
Evaluation, Execution and Cost views are now bundled, but only verified backend
live evidence can populate them. No synthetic evidence was inserted to demonstrate
these views. No authenticated journey or real report has been verified.

## Exact release

- Source checkpoint: `d6903a5c25e58f2cff48503fa9a3e2155e93100d` (exporter `8446af8`, result UI `8a00504`).
- Lambda ZIP SHA-256: `26718dbd2cb9daa837561a6f663c1438b01b86199837301e391deea7739297f6`.
- Business and Worker CodeSha256: `JnGNvSy52qg3VhpvZjwUOLAbhhmYNzAeOR3up3OSl/Y=`.
- Updated functions: **Business, Worker**, code only. Both downloaded deployed ZIPs match.
- Artifact: `releases/26718dbd2cb9daa837561a6f663c1438b01b86199837301e391deea7739297f6/results-checkpoint.zip`, version `VOR5VLZdCsiOfzqhOhY8K13XQgNHNvvE` in the existing private release bucket.
- CloudFront invalidation: `I4JH72NP068FFW7UR784N0EY78`.

### Frontend hashes (S3 and actual CloudFront bytes match)

- `release-status.html`: `a772baa10351bf6cc6a73a70aa4c4c886fa2179818a171d07f07ccf4fc58b7ae`
- `assets/index-B5FJDnbv.js`: `be45ecb3f19a557a86d6ab14938e34f46f8e848952b4ee91dfa4e3ca2190f8d6`
- `assets/index-CxpBgIk2.css`: `5c84df3bbbcbe2732de233ecd74b29de31e62a21acc036ed9a4f28138d42c956`
- `index.html`: `83466ae3e02323d46d14e6a0f5db2cc93a9d84cfcd9f1f67a46aeed51e5026e8`

## Preservation and boundaries

Actual STS account matched existing Studio CFN, CloudFront and Cognito privately
using the approved profile/region. All **71 resource physical IDs**, entire live
CFN template, deployment parameters and outputs verified unchanged. No CFN update
or change set was needed for direct Lambda code updates. No resource replacement,
IAM, role, policy, queue/table, Cognito pool/client/user or settings mutation.
Auth, Authorizer, Dispatcher and FoundationExchange code/config stayed unchanged.
Business/Worker environment, role, handler, memory and timeout fingerprints match
pre-release values. Live/producer flags are absent (effective OFF); collector/eval
factory is unreachable while live is OFF. No protected settings read/write needed.
Existing admission flag on the separate FoundationExchange was preserved, not
confused with live execution enablement. Model/Policy/admission guards untouched.

Current shared hosted_auth source differs from old Business/Worker packages only
in callback diagnostics/flow tracking. These functions do not dispatch callback
routes. Auth and Authorizer themselves were deliberately NOT updated. Public
studio-config remains existing hosted/mode JSON; the separate static readiness
page describes source integration versus disabled/unverified operation.

Package uses existing uv frozen export + hash-required binary Linux ARM64 Python
3.13 install, including boto3/botocore 1.43.92 and OTel SDK 1.40.0. Lock and owned
Python source bytes checked against checkpoint; exporter/collector/SDK service
model present; ZIP CRC passed. No dynamic Lambda pip installation. Fault files,
work directories, environment/secret artifacts excluded. This is not Linux
Runtime execution proof or a real authenticated Lambda business journey.

## Verification

- 263 scoped offline tests passed, 2 dependency warnings (33.29 seconds).
- Standard TypeScript/Vite build passed; existing >500 kB bundle warning remains.
- Actual browser after deployment: public Cloudscape Agent Studio, invited
  sign-in link, no persona selector, and CLOUD-HOSTED DEMO / fixture warning.
  DOM references `/assets/index-B5FJDnbv.js` and `/assets/index-CxpBgIk2.css`.
- Public readiness page rendered actual release metadata, not run evidence.
- Browser screenshot capture saved, but vision inspection failed due to model
  catalog availability; text/DOM inspection succeeded. No human login used.
- Browser automation skill file was unavailable at its advertised location.
- Memory recall timed out; release evidence comes from source/cloud readbacks.

### Actual anonymous HTTP results

- `/`: **200** (bytes match uploaded artifact)
- `/studio-config.json`: **200**
- `/api`: **401**
- `/api/me`: **401**
- `/api/agents`: **401**
- `/api/admin/catalog`: **401**
- `/auth/callback`: **400**
- `/auth/verification/status`: **401**
- `/release-status.html`: **200** (bytes match uploaded artifact)
- `/assets/index-B5FJDnbv.js`: **200** (bytes match uploaded artifact)
- `/assets/index-CxpBgIk2.css`: **200** (bytes match uploaded artifact)

Callback without OAuth response returns 400; protected verification status returns
401. No secret query parameters, paid model/Gateway/Runtime/Browser-service/eval
calls, fault injection, human credentials, invitations or fake DDB run records.
No user data was read to claim an unchanged authenticated journey; identity
resources and code preservation plus anonymous probes are the proof boundary.

## Rollback and remaining gaps

Rollback code was saved locally and to a versioned private artifact before any
function update. Previous index version and code/config/source fingerprints are
in ignored `artifacts/results-release/before.json`; current proof in
`release.json`, `http.json`, `tests.log`, `build.log`, `package.log`, `deploy.log`.
- Business rollback `releases/fd2d1f826de8d3426b4587f8c4bdbf0fe5fc5f39768b424c3131ff0ec5903cdf/results-rollback.zip`, version `s053ohfeunV41dkSkM1nAfpxK5yohlvK`.
- Worker rollback `releases/fd2d1f826de8d3426b4587f8c4bdbf0fe5fc5f39768b424c3131ff0ec5903cdf/results-rollback.zip`, version `hRz2W8ECy4OKqXqjbg7nfNKhHja_i3kh`.
- Previous index version: `qZZYlzJtSbBFs5PQ5XDa4MeB2OrjmTAN`; SHA `938362ddf9bc652fbc8b82e83207935b14ea14ca7c0f94caebcf6448b4a03632`.

No old frontend assets deleted. Direct code updates intentionally leave CFN Code
artifact declarations unchanged: a future template deployment must preserve this
release explicitly rather than reverting code to the older parameter artifact.

Remaining: approved protected live integration state, full Runtime workload
admission/execution, actual span/export/evaluator evidence, production deterministic
evaluator wiring, and owner existing-login acceptance. No project-complete claim.

Well-Architected: Security preserved/gated; Reliability rollback and hash readback
verified, live flow unproved; Performance live unmeasured, bundle warning retained;
Cost no paid workload calls, infrastructure charges unknown; Operations checkpoint
and cloud receipt reproducible; Sustainability existing serverless resources only.
