# Live capabilities source milestone

Baseline: eef8f2a74919f6ecd4221c8a71d34568882a1b17. Branch: feat/live-capabilities.
Owner: 哥哥. Independent review: didi, to be requested by parent against the published fixed commit.
Status: SOURCE ONLY. NOT DEPLOYED. LIVE UNTESTED. No AWS discovery, writes or paid model calls performed.

## Implemented increment

- Business-user Cloudscape AI Catalog with Models / MCP servers / Tools / Skills; Agents appears only in live catalog mode where A2A record listing exists. Foundation Library stays separate.
- Catalog list/search/count/detail/current-version endpoints enforce discoverability before projecting safe fields. Client-forged hidden IDs cannot request access. No raw Registry descriptors, endpoint configuration or credentials are exposed.
- Available/requestable/blocked semantics. Requests carry server-owned user/workspace and business purpose, are stored in the existing repository (DynamoDB hosted, SQLite local), and pin catalog version. Distinct admin decisions require substantive reasons and are audited. Approval grants an existing record; no connector onboarding. Scoped live grants cannot follow a user to another workspace. Revocation and catalog changes retain current execution validation for existing fixture versions/jobs.
- `backend/live_catalog.py`: injected SDK Model Gateway and AWS Registry metadata providers. Registry uses ListRegistryRecords(APPROVED), GetRegistryRecord for tools descriptors, A2A and AGENT_SKILLS summaries. Model Gateway uses ListGatewayTargets/GetGatewayTarget; only explicit inference provider model mappings to the exact regional Bedrock runtime host are admitted, Claude/OpenAI families only. Nova, first-party OpenAI, Gemini and unknown connector mappings are omitted.
- Live exposure additionally requires operator-approved safe metadata, workspace exposure and data-handling policy. Provider prose is untrusted and not directly published. No live entry becomes executable merely because its metadata is discoverable or permission granted.
- `CATALOG_MODE=live` fails closed without an injected provider. Default remains existing clearly labeled fixture mode so currently working authentication and UI are not broken. Live mode cannot compose/deploy/invoke through the fixture runner. No automatic cloud calls at app startup.
- `backend/runtime_deployment.py`: SDK-shape-tested create/update/get boundary with explicit mutation gate, target/role/runtime allowlists, versioned S3 artifact references, numeric runtime version, definition/manifest digests and nonblocking readiness checks. Frozen manifest preserves full prompt, dataset, rubric, tool bindings and skill instructions. Missing/mismatched evidence never passes. This is NOT an executable Runtime harness and is NOT wired into SQS.
- `scripts/live_preflight.py`: offline SDK contract check by default; explicit read-only manifest mode checks exact STS account before existing resources, Gateway/Registry targets, model existence, IAM role and bucket owner. This mode was NOT executed. No writes, paid calls or credentials/account IDs in output.

## Offline validation and source handoff

Commands: `uv run --locked pytest -q`; `npm --prefix frontend run build`; `npm --prefix frontend run test:e2e`; `uv run --locked python scripts/live_preflight.py`.
SDK contract version: boto3/botocore 1.43.92, already pinned in uv.lock. No dependency installation or lock mutation was needed. Frontend remains pinned by package-lock.json.
Baseline: 212 tests passed. One existing external-policy request test is updated to assert the intentionally stricter 404/non-discoverability behavior, retaining rejection-reason and grant-policy coverage. New API/unit/Moto/browser tests are synthetic offline evidence, not AWS acceptance. Final results are recorded in docs/LIVE-CAPABILITIES.md.

## Exact first live slice acceptance (NOT RUN)

1. Parent confirms exact existing target account and resources, then peer reviews fixed SHA. No new site, identity pool, migration, account reset or password changes.
2. Verify current Registry and Model Gateway provider schemas against actual resources. Establish approved exposure policies and foundation compatibility. Metadata refresh failure must remain closed.
3. Pin one approved runnable foundation artifact, one Bedrock Claude route, one read-only MCP tool and one skill. Implement actual Model Gateway/MCP SDK transports and full frozen definition propagation; source ZIP must contain that executable harness plus pinned dependencies.
4. Wire durable SQS stages DEPLOY → WAIT_RUNTIME → INVOKE → EVALUATE with immutable version binding, persisted idempotency/lease state, continuation deadlines, per-invocation current grants, token/call budgets, trace/eval references and actual authenticated judge evidence. No Lambda background task or sleeping readiness loop.
5. Human UI: discover catalog → request with purpose → separate admin approval → refresh → choose foundation/model/tool/skill → edit prompt/dataset/custom rubric → deploy real Runtime → measured Gateway calls → actual evaluation evidence → UI results. Change prompt and verify changed output; revoke and verify already-built current versions/queued jobs blocked.
6. Only run a bounded acceptance runner after explicit allow-live plus max calls and output-token limits. Missing judge, unknown model routing, stale definition or missing telemetry must block release. Do not claim managed judge routing through Gateway without evidence.

## Explicit remaining limits

- No production provider construction/configuration or refreshed live snapshot persistence. SDK adapters are injectable source contracts only; existing deployment remains fixture.
- Model discovery supports explicit inference provider operations only; connector catalog enumeration is not invented. Region/model existence preflight is not proof of Gateway invocation compatibility or model subscription access.
- Tool parsing supports explicit inline MCP tools object only; other descriptors are omitted. A2A catalog metadata is not remote agent execution. Skills are metadata, not arbitrary executable code.
- No real Strands/Runtime harness, Model Gateway client, MCP transport/auth, live source export, artifact upload/build, actual Runtime deployment or invocation. No live acceptance runner exists yet; LiveBudget is validation scaffolding, not distributed metering.
- No durable live deployment state machine, SQS continuation integration, authenticated trace retrieval, dataset evaluation runner or AgentCore managed judge adapter. Existing fixture SQS worker is unchanged except closed-mode validation.
- Runtime adapter requires an explicitly approved existing VPC configuration and does not create networking; actual service networking choice remains to be reviewed against the existing target. Its role allowlist is application defense, not proof of deployed IAM/PassRole enforcement.
- No provider cache. Discovery is bounded (10 pages/provider, 200 exposed records) but happens during a request transaction; production refresh isolation/cache and DynamoDB partition capacity require further implementation. Existing low-throughput global revision fence is retained, not a scale claim.

## Security-first / Well-Architected review

Security: safe metadata projection, exact-target fail-closed controls, no credential handling or identity-to-IAM conversion. Remaining: actual IAM/PassRole and transport/data-policy review.
Reliability: existing durable transactional request/grant path tested with Moto; live SQS lifecycle and recovery absent.
Performance: bounded catalog pagination/output, no polling sleeps; no production load evidence or provider cache.
Cost: no paid calls, explicit budget validator only; runtime cost enforcement not yet implemented.
Operations: SDK contracts, immutable bindings, audited admin reasons, honest readiness reporting; no live tracing/evaluation evidence.
Sustainability: reuse existing serverless/auth resources; no additional cloud infrastructure or utilization claim.

## Official API references verified during implementation

- https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore-control/client/list_registry_records.html
- https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore-control/client/get_gateway_target.html
- https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore-control/client/create_agent_runtime.html
- Installed botocore service model validates create/update/get/list argument shapes offline.
