# Managed serverless hosting contract

See [deployment and acceptance](deployment.md) for current release procedures. The live agent journey is described in [Create Agent](create-agent-journey.md).

## Scope and exact port mapping

Preserve the accepted Cloudscape UI and local SQLite implementation/tests. Hosted mode uses the same FastAPI business routes via Mangum, with a structured repository interface replacing application SQL. No SQLite database, SQL engine, local state file, background task, or model invocation in hosted Lambda.

- SQLite tables become DynamoDB entity partitions: components, foundations, catalog_history, grants, agents, versions, jobs, events, requests, audit, settings, oidc_flows, hosted_sessions, principals. Individual rows are versioned JSON documents, not a single database blob. Session identifiers remain SHA-256 hashes. DynamoDB encryption at rest and PITR enabled; token/PKCE values never logged.
- `Store.tx()` becomes a repository unit of work: local SQLite transactions retain current behavior; hosted consistent DynamoDB reads plus a conditional global revision fence serialize commits for this bounded, low-traffic demo. Commit writes changed records and the fence atomically; stale reads/writes return retryable conflict, never silently overwrite. This preserves base-version locking, one active job per agent, idempotency binding, grants, policy and quota checks. Read-only transactions also verify the revision. It intentionally favors correctness over throughput; entity-partition reads and a global fence are not claimed as a production-scale design.
- Existing five-stage fixture worker becomes an SQS-triggered Lambda calling the same `step_job`. Each stage is committed durably, rechecks subject/workspace membership, catalog/grants, definition version, deadline, and gate policy. Duplicate deliveries are harmless. Remaining-time guard returns a partial-batch failure for durable retry instead of background execution. Maximum dataset remains 20 cases. Worker rechecks live server session expiry/revocation; no expired cached principal authority.
- A committed job INSERT is a durable outbox event: DynamoDB Streams dispatcher sends its ID to SQS; failed dispatch retries, with failure destination and alarms. No enqueue/commit loss window. SQS has bounded retries, DLQ, encrypted storage and visibility > worker timeout. Terminal jobs are no-ops on replay.
- Export still checks agent owner/workspace. Hosted ZIP bytes are stored under a subject/agent/version-scoped encrypted private S3 prefix, then returned through the authenticated API; no public bucket or reusable unauthenticated export URL.

## Identity and front doors

CloudFront default HTTPS URL -> private S3 REST origin with OAC for UI; `/api`, `/api/*`, `/auth/*` and `/studio-config.json` -> API Gateway managed HTTPS origin. There is no VPC, EC2, public instance origin, NAT, VPC origin, BPA exclusion, or BPA modification in the new stack.

API Gateway Lambda REQUEST authorizer on every business route validates hashed server-session cookie, expiry, signed Cognito access-token issuer/client/token-use/scope, approved group and server-owned membership. Authorizer cache TTL is zero. Backend independently authenticates and retains CSRF/same-origin, owner/workspace isolation, self-approval only under the server-verified active Platform Admin role (recorded with an explicit audit marker), revocation persistence, immutable definitions, current-version evaluation and invocation gates. Only exact GET auth bootstrap/callback and public non-business configuration routes are unauthenticated and invoke a separate auth handler, not the business handler. Unrecognized routes do not expose data.

Retain server-side code+PKCE, one-use state, cookie binding, nonce, ID-token audience/issuer, verified email and subject matching. HttpOnly Secure __Host cookies; browser never receives provider tokens or chooses a persona. Invite-only new Cognito pool/client/domain, research/operations/admin groups; exactly one approved group. User enrollment and group assignment are operator-controlled; application releases preserve the existing pool and users.

## AgentCore and MCP

With `journeyPlatform` enabled, SQS workers deploy and invoke agents on AgentCore
Runtime, whose pinned manifests select allowed model and Gateway operations. MCP
onboarding publishes discovered tools through AWS Agent Registry; DynamoDB keeps
workspace visibility and execution bindings. Secrets Manager and AgentCore Identity
hold credentials for Gateway injection. No PAT reaches an agent prompt.

Persistent resources and release artifacts are retained, encrypted and
access-controlled. Installers manage resource ownership metadata. Use the deployment
guide for template checks, scoped changes, authenticated acceptance and rollback.
