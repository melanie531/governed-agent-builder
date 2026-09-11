# Agent Studio hosted mode

**Implemented, offline-tested, not deployed. No cloud URL yet.**

## Entry and identity

The hosted frontend reads `/studio-config.json`, renders an Agent Studio landing,
and offers **Sign in / Open Studio**. Cognito managed login authenticates invited
members. The backend exchanges the authorization code using PKCE, validates the
access token and ID-token nonce, then creates a Secure/HttpOnly/SameSite session.
Provider tokens stay server-side. There is no hosted persona menu or public
`/api/demo/*` bootstrap. Hosted mode seeds no synthetic user grants.

Cognito groups are assigned by an authorized operator outside this app:

| Group | Server-owned membership |
|---|---|
| `studio-research` | Business member, Research studio, initial fixture model/tool/skill grants |
| `studio-operations` | Business member, Operations desk, narrower initial fixture grants |
| `studio-admin` | Governance administrator, no blanket access to private agent content |

Exactly one of these groups is required. Users cannot grant themselves a group,
role or workspace. This first version does not support multiple workspaces per
subject; ambiguous memberships fail closed. Each user owns records by immutable
Cognito `sub`, not email or a browser-supplied persona identifier.

Fresh sign-in does not restore a capability revoked by an administrator. Current
application grants/policy are checked on every protected operation and worker
transition. Expired membership evidence stops a pending job; sign in and retry.
An administrator can only grant capabilities to known business members.

Access sessions expire at most 15 minutes after token issuance with the supplied
identity template. There is no silent refresh. Browser 401 resets the workspace
to sign-in. Logout invalidates the server session and then visits Cognito logout.
Cognito group changes/user disablement are bounded by token expiry, not instantly
rechecked through an admin API. Local application grants are immediately checked.

## Explicit launch configuration

This contract is for future provisioning, not instructions to expose a laptop:

- `HOSTED_PREVIEW=1`; **unset DEMO_MODE**. Mixing the two fails.
- `EXECUTION_MODE=local`: fixture execution only. AWS agent execution stays blocked.
- `PUBLIC_URL`: exact CloudFront HTTPS origin, no path/query/credentials.
- `STATE_PATH=/data/state.sqlite`: durable encrypted local-block volume.
- `COGNITO_REGION`, `COGNITO_USER_POOL_ID`, `COGNITO_CLIENT_ID`, `COGNITO_DOMAIN`:
  nonsecret outputs from the new isolated identity stack only.
- Start `python -m backend.hosted` as an unprivileged process behind the approved
  same-host TLS proxy. It binds `127.0.0.1:5187`, one worker, with proxy-header trust
  disabled and access logging off. This is not a Lambda handler or multi-worker service.

The reverse proxy must accept only the configured CloudFront Host and set the
same exact host for the app. Do not pass arbitrary identity headers or infer a
public URL from forwarded headers. The proxy TLS certificate must be trusted by
CloudFront and cover the configured origin hostname. Access logs must exclude
query strings, cookies, Authorization and token exchange bodies. Never log full
OAuth callback URLs, which contain a short-lived authorization code.

## Infrastructure prepared

`infra/edge.py` renders CloudFormation for private S3/OAC, TLS-only private VPC
origin, security headers and cache-disabled API/auth/config behaviors. Frontend
SPA rewrite applies only to the frontend behavior. S3 requires TLS and only the
specific CloudFront distribution can read objects. No shared IAM changes.

`infra/identity.py --application-origin https://<new-distribution>.cloudfront.net`
renders a new invite-only Cognito pool/client/domain/groups. The exact callback
is `/auth/callback`. No users/passwords are embedded in the template. Public
self-registration, implicit OAuth and password APIs are not enabled.

**These two templates are not an end-to-end deploy command.** Edge requires an
already healthy private TLS origin. EC2/VPC/EBS bootstrap and TLS provisioning
remain to be written after an approved origin DNS/validation path is provided.
No Route 53 zone or issued regional ACM certificate was available in the verified
account. Never substitute HTTP or a self-signed certificate to get a green deploy.

## Safe operator onboarding

After the stack and secure origin exist, confirm the exact recipient and intended
membership with the parent/operator. Create the invited member only in the new
pool, use Cognito's email invitation, and assign exactly one approved group.
The user sets their password in managed Cognito login; never ask for or transmit
a password or authentication code through chat/logs. Do not create a default admin,
reuse old demo acceptance credentials, or borrow another app's browser session.

A real browser acceptance run requires the invited operator's interactive login.
Offline token fixtures and intercepted UI responses are test doubles, not evidence
of this step. No user creation or email invitation has been performed.

## Verification distinction

- `tests/test_hosted_auth.py`: locally generated RSA-signed JWTs, auth-route matrix,
  token constraints, role/subject isolation, grants, CSRF, session expiry/logout.
- `tests/test_hosted_callback.py`: mocked Cognito token exchange, PKCE/nonce/subject
  binding, cookie flags and callback replay rejection.
- `tests/test_cloud_templates.py`: private origin, TLS-only, invite-only identity,
  cache/SPA behavior invariants.
- `frontend/e2e/hosted-entry.spec.ts`: **UI-only mocked API** entry/menu contracts.
- Existing browser journeys still execute real local API/worker interactions.

Required hosted acceptance remains: real Cognito login, unauthenticated edge/API
denial, actual business/admin identities, direct origin isolation, lifecycle and
cross-user tests, logout, database/job restart, encrypted backup/restore, security
review, and verified remote code/image provenance. None is claimed complete.

## Data and secrets

The encrypted server volume holds the SQLite database, including short-lived
access tokens and CSRF secrets. Treat backups as sensitive. Run with restrictive
filesystem permissions, encrypt/retain backups deliberately, and purge expired
session rows; login already removes expired sessions. This prototype is for
synthetic datasets only. Exported ZIPs contain fixture configuration/source, not
session records or provider tokens; an export executes locally outside platform
policy enforcement.

Cognito is approved prototype identity, not customer Okta. Future Okta federation
and live AgentCore/model/tool/evaluation integration are separate work. This does
not provision a SageMaker Studio Domain or any SageMaker compute service.
