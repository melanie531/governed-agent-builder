# Unconnected integration boundaries

## Production identity: Okta OIDC

Production mode is deliberately unavailable. Do not expose DEV ONLY persona selection to real users. Before adding a non-demo launcher:

1. Register a confidential **web application / backend-for-frontend** in the organization's approved Okta tenant. Use Authorization Code + PKCE, exact HTTPS redirect URI such as `https://<approved-host>/auth/callback`, state and nonce. Do not use a wildcard redirect.
2. Configure server-side placeholders `OKTA_ISSUER=https://<tenant>/oauth2/<authorization-server>`, `OKTA_AUDIENCE=<api-audience>`, `OKTA_CLIENT_ID=<registered-client-id>`, and approved API scope `agent-builder:use`. Any client secret belongs in an approved secret store and never chat, browser bundles, log files, git or command arguments. These variable names are documentation, not an implemented configuration contract.
3. Validate access-token signature against issuer JWKS, algorithm allowlist, exact issuer/audience, expiry/not-before and intended API scope; cache keys with rotation and bounded TTL. Do not use an ID token as API authorization. Handle logout and revocation.
4. Resolve workspace/role assignments from a trusted platform policy store and verified stable subject, not editable browser fields or arbitrary group claims. Map only administrator-approved groups. Deny unknown identities or missing attributes.
5. Replace `/api/demo/*` entirely in production. Use a Secure HttpOnly SameSite session cookie with CSRF protections. Add HTTPS, strict allowed hosts/origins, security headers and production session persistence/rotation. The local test selector must be unreachable.
6. Add real-identity authorization integration tests before accepting production requests. Existing demo tests verify the policy logic, not Okta.

## AWS roles and service boundaries

One future AWS account, independently scoped per-agent/version runtime deployments. Hosting is not selected; Fargate + SQS + DynamoDB are options, not deployed requirements. No globally shared runtime or mandatory CloudFront/S3 frontend is implied.

- **Backend / deploy role:** narrowly allow the verified AgentCore control-plane create/update/get operations for approved runtime resources and approved harness artifacts. Restrict `iam:PassRole` to enumerated execution roles and the intended service using supported condition keys. Verify actual resource-level support from current IAM service authorization docs before writing a policy. Do not attach administrator access.
- **Runtime role:** separate execution identity, restricted to approved model/tool Gateway access and logging/data resources required by that agent. Never grant deployment permissions to runtime code or business-user sessions.
- **Test/evaluator role:** only version-scoped invocation and evidence/evaluation operations required for a job. Verify Runtime qualifier behavior and returned configuration digest before accepting results.
- **IAM** enforces AWS operations, not application compatibility lists. The application's grant/policy intersection remains necessary.
- **AgentCore Identity** can support configured downstream credential/identity flows. It does not convert Okta user claims into IAM permissions.
- **Foundation Library** owns versioned harness source/artifact manifests. Models and foundations are not Registry resources.
- **Model Gateway and Tool Gateway** are two AgentCore Gateway integration boundaries. Validate actual supported target protocols, authentication, discovery and routing before claiming a model is supported. A label or route alias is not a working connector.
- **AgentCore Registry** integration concerns approved tools/skills and their metadata, not foundation code or the model catalog.
- **Bedrock Claude + Bedrock OpenAI** remain inside AWS. Do not substitute first-party OpenAI. Exact regional/global route IDs, endpoint APIs and capabilities must be validated in the target account. Demo aliases are not production identifiers.
- **Gemini** is the only external model candidate. Developer API credentials and Vertex AI OAuth/workload identity are different integrations; choose and validate one. Workspace data policy and explicit grants must allow external egress.

## Runtime adapter entry point

`AgentCoreRuntimeAdapter` uses boto3's real `bedrock-agentcore` runtime client and `invoke_agent_runtime` operation. The installed SDK shape is checked offline. It validates explicit region, Runtime ARN, qualifier, two distinct HTTPS Gateway endpoints and an explicit call authorization flag before constructing a client. No client is constructed or AWS credentials read by the normal application.

This is **an invoke integration entry point, not a working AWS deployment adapter**. Before use, implement and verify:

- signed/pinned harness artifacts and dependency provenance;
- actual create/update deployment and resource idempotency;
- readiness backoff/deadline;
- immutable version/qualifier targeting with deployment locks where needed;
- a returned definition-digest contract, validation and strict mismatch rejection;
- gateway authentication and approved target discovery;
- runtime network/egress restrictions and tenant isolation;
- test identity scope and credential handling;
- bounded response streaming, sanitization, retries and invocation limits;
- runtime → CloudWatch → AgentCore Evaluations correlation and complete evidence gates;
- explicit teardown, retention and cost caps.

CloudWatch and existing Splunk are **not connected**. The UI trace is a local SQLite event stream, not cloud logs. No LLM judge is configured; required judge evidence blocks.

## Integration-test gate

No live integration test is enabled in the test suite or GitHub workflow. Required preconditions: explicit operator approval for cloud resources/model spend, a configured approved AWS account/region and least-privilege workload identity, Okta production authentication, both verified Gateways, approved real model routes, quotas, runtime artifact and version/digest checks, isolated synthetic integration data, budget and teardown plan. Until all are ready, `EXECUTION_MODE=aws` fails closed. Never silently substitute the local fixture runner in real mode.
