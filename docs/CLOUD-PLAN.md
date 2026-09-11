# Cloud-hosted fixture preview plan

Status: **Deployment in progress. Private origin HTTP explicitly approved 2026-09-11; viewer and Cognito HTTPS mandatory.**
Verified 2026-09-11. Application baseline: `feat/local-first` at
`e79dc4c7837e45cbb1b16055f4c3a2f2bb97b90b` with a clean working tree before this document.

## Verified inputs

Read README.md, INTEGRATIONS.md, VERIFICATION.md, backend/app.py,
backend/store.py, backend/__main__.py and frontend/src/main.tsx.

- The frontend already calls same-origin `/api`; there is no need for a browser localhost backend in cloud mode.
- FastAPI uses SQL throughout application transactions, not a fully abstract database repository. SQLite WAL plus one process and its asynchronous worker require local persistent block storage. Do not place WAL storage on EFS/NFS or ephemeral Lambda storage.
- Existing startup explicitly allows only local demo mode and loopback hosts. Those protections must remain intact. Hosting requires a separate, fail-closed `HOSTED_PREVIEW` configuration, not changing the local host allowlist to a wildcard.
- Previously recorded 92 backend and 2 browser passes are local baseline evidence, not cloud test results. They have not been rerun for this planning-only change.
- The existing `agentic-platform-prod` SDK profile resolves to the documented user-owned production account in `us-west-2`. STS account comparison was performed in memory without printing account IDs or credentials.
- Existing stacks: `AgenticPlatform-Web` UPDATE_COMPLETE, `AgenticPlatform-ControlPlane-Provisioned` CREATE_COMPLETE, CDKToolkit CREATE_COMPLETE. No new governed-agent-builder stack was listed.
- Existing role metadata shows administrator permissions. This is not permission to change shared IAM, attach administrator policies, or deploy unrestricted workload roles. No permissions were changed.

## How the previous demo is hosted

Reference only: the existing demo's `frontend-edge.ts` and `platform-apis.ts`.
Its application is https://d161jezsglocrd.cloudfront.net/ .

CloudFront serves private S3 through Origin Access Control (OAC), sends `/api/*`
to API Gateway HTTP API, disables API caching, and uses a Cognito login client.
The backend is Lambda-based. That backend pattern is **not** safe to copy with
this application's SQLite and in-process worker unchanged.

The live Cognito client is `agentic-platform-web`, has no client secret, allows
OAuth authorization code and has only the existing application's exact callback
URL. Its only supported identity provider is Cognito. A new preview hostname
cannot complete an OAuth login with that callback configuration unchanged.
Password/SRP flows being enabled is not approval to repurpose the existing client,
collect passwords in this application, or borrow the old demo's acceptance broker.

## Approved identity and remaining blocking input

The user explicitly approved Cognito sign-in for this prototype. The hosted
experience is now Agent Studio landing → managed Cognito code/PKCE → assigned
workspace. The hosted persona selector and demo login routes are closed. New
invite-only pool/client/groups are defined in `infra/identity.py`; **deployment in progress**.

Server-controlled group policy maps the verified Cognito subject to exactly one
workspace/role. Multiple approved groups fail closed rather than offer an unsafe
role selector. `studio-admin` cannot create/read another member's private agents.
The current-user menu offers logout only. No real users have been invited or
assigned groups. Parent must confirm the invitation email and intended membership
before onboarding; no password or authentication code goes through chat.

## Approved security exception (2026-09-11)

Operator explicitly approved CloudFront → private backend HTTP for this synthetic
preview. This replaces the former origin TLS/DNS blocker only. CloudFront viewer
HTTPS/default AWS certificate and Cognito HTTPS remain mandatory. No public IP,
public backend, SSH, anonymous business API or public administrator is approved.
Only CloudFront's service-managed VPC-origin security group may reach TCP/80.
No custom DNS or self-signed origin certificate is required.

Selected: private S3/OAC + CloudFront + private t4g.small EC2 + encrypted retained
20 GiB gp3 EBS, single AZ and one Python worker. An isolated NAT gateway supplies
TLS-only bootstrap/Cognito/SSM egress. Planning estimate: NAT + public IPv4 about
USD 36/month before data; instance about USD 12–13, EBS about USD 3, plus requests,
logs and transfer; approximately USD 55–70/month at low traffic, not a quote/cap.
No existing demo resources or shared roles are changed.

### Resource boundary

All new resource names start with `governed-agent-builder` where the service
supports explicit names. Tag new resources `project=governed-agent-builder`,
`owner=melanie531`, and `managedBy=cloudformation` (or CDK if used).

- Separate IaC stack(s), VPC/subnet/security groups, CloudFront distribution/OAC,
  private frontend/artifact S3 buckets, EC2 instance role/profile, instance,
  retained encrypted EBS data volume, scoped log group and new approved Cognito identity resources.
- No edits to the old demo stack, app client, pool, bucket, distribution, domain,
  API, shared auth policy, runtime or control-plane integrations.
- Do not reuse the existing CDK bootstrap deployment role simply because it is
  present. Inspect its privilege path before use, or use native CloudFormation
  with narrowly scoped new workload roles and explicit IAM resource review.
- Instance permissions: read only its versioned deployment artifacts; write only
  its log streams; required management-channel permissions only. No Bedrock,
  AgentCore, Cognito administration, IAM administration or deployment privileges.
- PassRole only to exact newly created instance/service roles, with appropriate
  passed-to-service conditions; no shared permission-boundary edits.

### Network and packaging checks before deployment

- Private-subnet instance without public IPv4 or inbound SSH. Restrict origin
  ingress to CloudFront's documented managed origin connectivity; never modify
  the CloudFront service-managed security group.
- Validate the approved private HTTP hop and viewer HTTPS independently; restrict
  origin ingress to CloudFront service-managed SG. This hop is unencrypted by exception.
- Choose reproducible prebuilt artifacts and an AMI supporting Python >=3.12.
  Verify dependency bootstrap in a private subnet, not an untested `apt`/`pip`
  internet download at startup. No global tool installations on the operator host.
- An S3 gateway endpoint can deliver scoped artifacts without NAT. Management,
  CloudWatch and JWKS retrieval still require a deliberate reachable endpoint or
  constrained egress plan. Price these before deployment; do not assume a fully
  isolated subnet can fetch Cognito JWKS or package dependencies.
- Use a process supervisor running one unprivileged FastAPI worker. Keep the
  launcher implementation separate from the loopback-only local launcher.
- Forward only required origin metadata from the trusted CloudFront path. Never
  trust arbitrary `X-Forwarded-*` identity or role fields. Validate the exact
  configured external HTTPS origin and host.

### Authentication and browser boundary

- Implemented login uses authorization code + PKCE, exact redirect, state/nonce, secure
  server-side sessions and bounded expiry. No tokens in URLs delivered to the
  operator, browser local storage, logs or repository.
- Every `/api` and `/api/*` request, including persona bootstrap, unknown routes
  and admin endpoints, must pass real preview authentication before body parsing
  and business logic. Unauthenticated calls return 401/403, not an HTML login page.
- Authentication bootstrap/callback endpoints are necessarily reachable for login
  but cannot execute application business operations. Static login assets may be
  public; persona selection and application data may not be public.
- Validate issuer, signature algorithm, keys/rotation, token purpose, intended
  audience/client, expiration, scopes and approved operator subject. Do not use
  an ID token as an API access token. Use opaque server-side sessions bound to the verified subject. Store only a hash
  of the browser session identifier; never put provider tokens in browser storage.
- Preserve ownership/grant/revocation checks, using verified subject IDs rather
  than synthetic persona IDs. Hosted persona selection is unavailable.
- HttpOnly, Secure, SameSite cookies; exact Origin and session-bound CSRF checks
  on mutations; logout invalidation. No wildcard CORS or host allowlist.
- CloudFront API and auth behaviors disable caching and forward required cookies,
  Authorization and CSRF headers. Cover both `/api` and `/api/*`. Route SPA fallback
  only on frontend behavior; never rewrite API 401/403/404 to index.html.
- Display **CLOUD-HOSTED DEMO · fixture runner, no live LLM**. Change local-only UI
  hosting claims in hosted mode without claiming real AgentCore/Okta integration.

### Data and jobs

- Separate encrypted gp3 EBS data volume retained across instance replacement;
  not a root-volume-only SQLite file. `DeleteOnTermination=false`/retain policies
  alone do not attach or mount replacement storage: implement and verify both.
- SQLite stays on local block storage, one writer process; prevent two instances
  mounting or writing the database. Plan a brief stop/start replacement, not an
  overlapping autoscaling rollout. Retained volume is tied to its AZ.
- Existing persisted deadlines and replay of unfinished side-effect-free fixture
  jobs are appropriate for this preview. Verify restart during a job and completed
  job/definition durability on the hosted instance.
- Backups must use SQLite's backup API or a coordinated quiesce, not copy only the
  main database while WAL writes continue. Encrypt backups; document restore and
  retention. Retention is not a tested backup/restore guarantee.
- Keep existing request/case/concurrency/rate caps. No live models, production
  agent deployment, external connectors or customer datasets.

## Cost and Well-Architected checks

Planning allowance only, **not a live AWS quote**: a small always-on instance plus
EBS is generally tens of USD/month; private interface endpoints or NAT can add
tens more. Budget roughly **USD 30–100/month** for a low-traffic preview pending
actual SKU/region/endpoint/TLS design. CloudFront/S3/log requests and data transfer
are usage-based; there is no paid model usage in this plan. Confirm an itemized
estimate before provisioning, and prefer fewer fixed-cost network components
where security permits. Amplify frontend-only would not remove these backend costs.

| Pillar | Planned status / unresolved check |
|---|---|
| Security | Private origin + private S3 + real operator login; identity implemented offline; approved private HTTP exception; real login acceptance remains open |
| Reliability | Durable local SQLite + bounded replay; single AZ/process intentionally accepted for preview, recovery must be tested |
| Performance | Existing two fixture slots; measure hosted browser/API latency before claiming performance |
| Cost optimization | Small instance, no model calls; price networking rather than hiding endpoint/NAT costs |
| Operational excellence | IaC, scoped sanitized logs, pinned artifact, health/restart/restore checks; not deployed or observed yet |
| Sustainability | Right-size one preview process; no idle HA fleet; document stop/cleanup procedure |

## Acceptance and delivery gate

1. Review/synthesize new IaC and scoped IAM; verify no changes to old stacks.
2. Deploy only new isolated resources with the approved private HTTP boundary enforced.
3. Perform real operator login using safe browser/host-owned entry. Ask the operator
   only to perform necessary login, never to send a password or code.
4. Test public CloudFront `/api/demo/personas`, `/api/demo/session`, `/api/me`,
   agents/jobs/export/invoke/admin, malformed/unknown API paths: unauthenticated
   requests denied before business handling. No direct public origin bypass.
5. Walk creation, passing/failing/missing-judge tests, revision, export, invoke,
   grants/revocation and cross-persona resource denial at the real HTTPS URL.
6. Verify CSRF, cookie flags, host/proxy spoofing, session rotation, cache isolation,
   restart/job replay, database persistence, logs and backup/restore.
7. Obtain independent security review before final publication. Publish no success
   claim from merely successful provisioning or a local synthetic auth bypass.
8. Audit branch commits, push only this work to the private feature branch, verify
   remote SHA. No PR, merge, existing-demo deploy or public repository change.
9. Deliver token-free HTTPS URL, SHA, resource health, measured tests and explicit
   fixture limitations. Cleanup guide must cover CloudFront/VPC-origin teardown
   ordering, stop costs, retained EBS/backups/logs and new identity resources.

## Current implementation result

- `backend/hosted_auth.py`: RS256 issuer/client/token-purpose/scope/time validation,
  PKCE/state/nonce, secure server-side sessions, group-derived backend workspace,
  hashed session lookup, logout, no session re-seeding of revoked grants.
- `backend/app.py`: real auth before API body parsing/business logic, exact-host
  validation, hosted CSRF, closed demo endpoints, current subject ownership,
  original loopback local mode preserved. No arbitrary forwarded-header trust.
- `backend/hosted.py`: separate fail-closed, one-process launcher; requires `/data`
  state path and binds loopback behind a same-instance private HTTP proxy.
- Frontend: branded Agent Studio entry, current-user/logout menu, no hosted user
  list, direct assigned workspace entry. Original Cloudscape local journeys remain.
- `infra/identity.py` and `infra/edge.py`: isolated CloudFormation templates;
  AWS ValidateTemplate passed for both. This validates template syntax, **not**
  successful service provisioning, IAM sufficiency or full deployment readiness.
- Live discovery corrected and verified CloudFront's managed CachingDisabled and
  AllViewer policies; API/auth/config behaviors do not cache or use SPA rewrite.
- EC2/VPC/EBS/private proxy/bootstrap/online backup provisioning is now defined in
  `infra/compute.py`, `bootstrap.sh`, and `backup.py`; live evidence is recorded separately.
  The edge references the new private instance. Runtime stays fail-closed until
  the exact distribution URL and isolated Cognito client are configured.

Cognito access tokens are configured for 15 minutes; this BFF does not refresh
provider tokens. Expiry requires sign-in again. Application grant revocations are
immediate; Cognito group removal/disablement can take until the current access
session expires, because validation does not make a paid/privileged admin lookup
on every request. No claim of immediate provider-side revocation is made.

Offline tests use generated test signing keys and mocked token endpoints. They
are **not** hosted Cognito E2E evidence. See CLOUD-VERIFICATION.md for live
results and remaining operator-login acceptance gates.
