# Managed serverless hosting contract

Status: implementation milestone tested (149 tests passing, including 130 preserved tests and 19 new serverless tests); cloud deployment in progress, real authenticated browser journey not yet verified.

## Scope and exact port mapping

Preserve the accepted Cloudscape UI and local SQLite implementation/tests. Hosted mode uses the same FastAPI business routes via Mangum, with a structured repository interface replacing application SQL. No SQLite database, SQL engine, local state file, background task, or model invocation in hosted Lambda.

- SQLite tables become DynamoDB entity partitions: components, foundations, catalog_history, grants, agents, versions, jobs, events, requests, audit, settings, oidc_flows, hosted_sessions, principals. Individual rows are versioned JSON documents, not a single database blob. Session identifiers remain SHA-256 hashes. DynamoDB encryption at rest and PITR enabled; token/PKCE values never logged.
- `Store.tx()` becomes a repository unit of work: local SQLite transactions retain current behavior; hosted consistent DynamoDB reads plus a conditional global revision fence serialize commits for this bounded, low-traffic demo. Commit writes changed records and the fence atomically; stale reads/writes return retryable conflict, never silently overwrite. This preserves base-version locking, one active job per agent, idempotency binding, grants, policy and quota checks. Read-only transactions also verify the revision. It intentionally favors correctness over throughput; entity-partition reads and a global fence are not claimed as a production-scale design.
- Existing five-stage fixture worker becomes an SQS-triggered Lambda calling the same `step_job`. Each stage is committed durably, rechecks subject/workspace membership, catalog/grants, definition version, deadline, and gate policy. Duplicate deliveries are harmless. Remaining-time guard returns a partial-batch failure for durable retry instead of background execution. Maximum dataset remains 20 cases. Worker rechecks live server session expiry/revocation; no expired cached principal authority.
- A committed job INSERT is a durable outbox event: DynamoDB Streams dispatcher sends its ID to SQS; failed dispatch retries, with failure destination and alarms. No enqueue/commit loss window. SQS has bounded retries, DLQ, encrypted storage and visibility > worker timeout. Terminal jobs are no-ops on replay.
- Export still checks agent owner/workspace. Hosted ZIP bytes are stored under a subject/agent/version-scoped encrypted private S3 prefix, then returned through the authenticated API; no public bucket or reusable unauthenticated export URL.

## Identity and front doors

CloudFront default HTTPS URL -> private S3 REST origin with OAC for UI; `/api`, `/api/*`, `/auth/*` and `/studio-config.json` -> API Gateway managed HTTPS origin. There is no VPC, EC2, public instance origin, NAT, VPC origin, BPA exclusion, or BPA modification in the new stack.

API Gateway Lambda REQUEST authorizer on every business route validates hashed server-session cookie, expiry, signed Cognito access-token issuer/client/token-use/scope, approved group and server-owned membership. Authorizer cache TTL is zero. Backend independently authenticates and retains CSRF/same-origin, owner/workspace isolation, no self-approval, revocation persistence, immutable definitions, current-version evaluation and invocation gates. Only exact GET auth bootstrap/callback and public non-business configuration routes are unauthenticated and invoke a separate auth handler, not the business handler. Unrecognized routes do not expose data.

Retain server-side code+PKCE, one-use state, cookie binding, nonce, ID-token audience/issuer, verified email and subject matching. HttpOnly Secure __Host cookies; browser never receives provider tokens or chooses a persona. Invite-only new Cognito pool/client/domain, research/operations/admin groups; exactly one approved group. No users, invitations or admin memberships are created without the owner's explicit email/group decision. No reuse of previous demo identity or resources.

## Resource and release boundaries

Separate `governed-agent-builder-serverless-*` stack names; scope IAM to own tables, queues, buckets and log groups. No IAM AdministratorAccess or changes to shared boundaries. All old failed resources remain untouched pending approved cleanup. Existing NAT, EC2, EBS and IPv4 costs remain separate from new low-traffic request/storage-based serverless charges.

Release proof required: preserve existing dirty files; baseline and adapted tests; repository privacy/lineage/secret checks; commit and verify remote branch SHA; validate CloudFormation and inspect no-VPC/BPA compatibility before create; verify actual CloudFront 200, hosted config/no persona selector, API unauthenticated 401, Cognito authorization redirect and real login page. Authorized browser create/test/invoke/export/revoke journey requires approved invitation and human sign-in and must remain explicitly unverified until performed. Offline signed-token tests are not real Cognito authentication.

## Well-Architected scope

Security: private buckets, encrypted durable state, invite-only Cognito and two-layer session checks; safety boundary unchanged. Reliability: atomic concurrency fences, durable stream/queue dispatch, retry/DLQ and PITR; stream retention remains finite. Performance: bounded fixtures and low-traffic partition reads, no production-scale claim. Cost: no new idle compute/NAT; old charges continue. Operations: tests, scoped templates, versioned release digest, redacted status artifacts and alarms. Sustainability: on-demand execution instead of new always-on compute. Branding is non-blocking; fixture execution stays explicitly labeled, not AgentCore or live LLM.

## Initial milestone evidence

- Existing dirty deployment files were inspected and preserved, including the failed VPC-origin attempt; they are historical code, not executed by the new serverless deployer.
- `uv run pytest -q`: 149 passed; frontend TypeScript/Vite build passed (large-bundle warning remains). Moto/offline JWT tests are explicitly not real Cognito login.
- AWS CloudFormation ValidateTemplate passed for both isolated templates; actual account ownership checked internally against the previous project stack; effective BPA remained block-ingress.
- Hosted worker deadline is 15 minutes (local remains 60 seconds) to allow bounded SQS visibility retry; each invocation runs at most five fixture stages and requires 10 seconds remaining before a stage.
- GitHub remote verified private. This repository has no main branch: default and release branch are feat/local-first. Outgoing lineage is checked against origin/feat/local-first instead.
- DLQ alarms have no external notification recipient configured; operator must inspect alarms/queues. No invite/user has been created.
