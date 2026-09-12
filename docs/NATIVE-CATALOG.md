# AI Catalog: native discovery contract

AI Catalog is a business-facing aggregation of approved, discoverable capabilities.
It is not an AWS tool store. A tool is a callable operation, internal or external,
not necessarily an internet API and not a URL. Foundation Library remains the
platform-owned authority for blueprint configuration and code. CUSTOM metadata
can describe a blueprint but does not supply Foundation source without an explicit
platform mapping.

## Sources and connection state

`CATALOG_MODE=live` enables native discovery, never persisted fixture rows.
`create_app` injects `configured_catalog()` only in live mode, including the
serverless entry point which uses `create_app`. Missing or invalid approval
configuration returns sanitized 503 **NotConnected**. Source errors return 503,
not a seed fallback. Login/account data and Foundation execution are unchanged.
Existing fixture mode remains explicit development behavior, visibly labelled.
No deployment or environment change is part of this checkpoint.

`NATIVE_CATALOG_CONFIG` is server-owned JSON, not browser input. Its contract:

- Top-level `schema_version: 2`, `approved: true`, explicit `registries: []` and/or
  `model_gateways: []`, optional `cache_seconds` (0–60, default 30).
  Empty sources means not connected; it does not approve anything.
- Required `binding`: `expected_account` (12 digits), `region`, and nonempty
  `owner_approval` reference. This is server-owned owner approval, not user input.
- Each Registry source: `approved: true`, `region`, `registry_id`, full
  `registry_arn`, and `exposure`. ARN account/region/resource ID must match binding.
- Each Model Gateway source: `approved: true`, `region`, `gateway_id`, full
  `gateway_arn`, explicit `target_ids`, `exposure`, `auth: AWS_IAM`, and
  `list_models_url` exactly the documented HTTPS `/inference/v1/models` endpoint.
  No redirects, custom hosts, secret retrieval or inferred target IDs.
- Every exposure requires `approved: true`, nonempty string `version`, **explicit
  nonempty list of nonempty unique workspace strings**, boolean `requestable`,
  nonempty `owner` and `data_handling`, `digest_format: native-catalog-v2`, and
  lowercase 64-hex `approval_sha256`. Missing/null/string workspaces never mean all.
- Registry ID: `registry:<registry-id>:<record-id>`; tools append
  `:tool:<declared-name>`. Version is exact native recordVersion. Both parent and
  child approvals must pin the SAME `record_revision(detail)` hash, including
  full registry/record ARNs, record ID/type/version, name/displayName/description
  and complete descriptors. Any change without a version increment hides the
  item and children until reviewed. Children also intersect parent workspace scope.
- Model ID: `model:<gateway-id>:<target-id>:<unqualified-model-id>`. Each approval
  also requires `qualified_model_id` equal to the exact enumerated `targetName/modelId`.
  Version is `target_revision(detail)`: gateway ARN, target ID/name, inference
  configuration, and allowlisted nonsecret credential-provider configuration.
  `approval_sha256` is `model_revision(detail, model)`, binding that target hash
  and the **entire exact enumerated model JSON projection**, not a wildcard mapping.
  Changes in enumeration extensions also require review. Returned gateway ARN and
  target ID must equal the configured full identities, not just a name.

### Explicit v1 → v2 migration (no automatic approval)

Existing fixture behavior is unchanged. Old native config is intentionally rejected:
`descriptor_sha256` and inference-only digests do **not** grant v2 exposure. Do not
copy an old hash, reuse legacy registry IDs, set all workspaces, or auto-approve rows.
The owner must nominate the current new-namespace IAM registry/full ARNs and account/
region, review exact records/display content, choose workspace scopes and exact model
routes, and explicitly issue new v2 approvals. No source/config is enabled by this
source-only patch. Retain old configuration privately for rollback; unconfigured or
old approval configuration stays NotConnected until explicitly migrated.

Digest algorithm `native-catalog-v2`: SHA-256 of UTF-8 Python `json.dumps` of
`{"format":"native-catalog-v2","value":<projection>}`, with `sort_keys=True`,
`separators=(',', ':')`, `ensure_ascii=True`, `allow_nan=False`. This is a versioned
Python JSON serialization contract, **not RFC 8785**. Embedded descriptor JSON strings
retain exact whitespace; key ordering of outer objects is stable. No `default=str`
coercion. RecordVersion, package/server version, protocol/schema version and digest
are distinct. Approval utilities are `record_revision`, `target_revision`, and
`model_revision` in `backend/live_catalog.py`; they do not obtain or approve data.

Auth hash metadata includes provider type/ARN, scopes, grant type, return URL,
credential parameter name/prefix/location, IAM service/region, and custom parameter
**names only**. Tokens, credential values, and custom parameter values are omitted,
never decrypted. Auth metadata is not exposed in catalog responses. Discovery pins
are not execution credential-integrity attestations.

ALL source and exposure configuration is validated before SDK construction. Factory
uses one session with the approved region, compares STS GetCallerIdentity account
before any source reads, then shares that session across Registry, target metadata
and signing the model GET. Tests inject a synthetic session implementing the same
STS check; client/reader factories do not bypass it. Returned full Registry ARN and
record ARN are checked. No credential/profile/environment fallback on denial.

There are no default IDs, SSM/private skill reads, account-wide discovery, permission
changes or authenticated acceptance in this checkpoint. Source config is captured
at app construction; a later approved config change requires controlled reload.

### IAM and connection gate (not applied)

Registry uses exact-registry `agent-registry:ListDiscoverableRegistryRecords` and
exact-record `agent-registry:GetDiscoverableRegistryRecord` for BatchGet, not an
invented BatchGet IAM action. Optional Search is 1–20 results and 1–256 query chars;
normal UI search remains local and needs no Search permission.
GetGatewayTarget is gateway-ARN scoped; the backend additionally pins target IDs.
Model Gateway HTTP GET listing under IAM needs **bedrock-agentcore:InvokeGateway**,
which is broader invocation authority, **not IAM-enforced GET-only permission**.
Do not add it as a read-only permission. Existing ENFORCE gates/Denies stay unchanged.
Gateway outbound Mantle ListModels permission belongs to Gateway role, not the user.
The current interceptor/listing path remains unproven. Prefer registry-only first
connection while model invocation/listing authority is unresolved. Next gate is
owner-approved exact source config plus separate IAM/transport review and one-shot
read acceptance. No cloud connection, policy expansion or deployment is authorized here.

## Native contracts

Registry uses `agent-registry`, not legacy `bedrock-agentcore-control`:
`ListDiscoverableRegistryRecords(registryId, maxResults, nextToken)` then
`BatchGetDiscoverableRegistryRecord(entries=[{registryId, recordIds}])`.
Optional adapter search uses `SearchDiscoverableRegistryRecords(searchQuery,
registryIds, maxResults)`; ordinary UI search filters the authorized snapshot.
No admin List/Get records are used. Only APPROVED discovery results survive;
batch errors, mismatched versions or incomplete pagination fail closed.

| Native record / descriptor | Catalog meaning |
| --- | --- |
| MCP / mcpServer | MCP server; tools only from additionalData.tools.data.tools |
| AGENT / a2aAgentCard | Agent; A2A skills/capabilities do not become AgentSkills |
| AGENT / mcpServer | Agent with MCP protocol; reviewed declared operations may be Tools |
| SKILL / agentSkillsDefinition | Discovery metadata, never a complete installed package |
| CUSTOM / custom | Other resource, not executable Tool, regardless of name |
| Known record / custom | Declared record type, CUSTOM protocol, no callable inference |
| Unknown or invalid descriptor | Unsupported, nonselectable resource/type, no guesses |

MCP server JSON is `descriptors.mcpServer.data`; tools JSON is
`descriptors.mcpServer.additionalData.tools.data`. Tool rows preserve operation,
parent, declared input/output **redacted display-only schema, not execution validation**,
owner, record version,
descriptor schema version, source hash and unverified execution binding.
External schema refs, defaults/examples, source URLs and raw package content are
not exported or fetched. Dropped constraints/refs make this projection unsuitable
for executing or validating arguments; a separately approved future executor must
retain and validate the private original. SKILL.md is discovery metadata only; immutable approved
artifact references and a verified execution binding are required for future
skill execution. Every supplied supported descriptor version is checked, including
markdown-only SKILL. Structured payloads require valid JSON and minimal selected
protocol structure: MCP server name/version and callable schemas, A2A wrapper 0.3
with embedded protocolVersion 0.3.0/card fields, SKILL 0.1.0 typed known metadata,
CUSTOM valid JSON with no standardized protocol. Unknown SKILL extensions remain
private and unmodified. Malformed or unsupported descriptors are nonrequestable.
This is minimal discovery validation, not full execution admission. This slice marks **all native entries execution-not-ready**.

Models are separate AgentCore Model Gateway inference resources, not Tool Gateway
or Registry rows. Approved exact target GetGatewayTarget reads accept the native
`bedrock-mantle` connector or configured `bedrock-mantle.<region>.api.aws` provider.
The native HTTP GET `/inference/v1/models` is the actual enumeration API (there
is no installed botocore ListModels operation). Only enumerated target-qualified
Bedrock `owned_by=system` Claude/OpenAI routes with exact exposure are admitted.
Gemini/direct external connectors are not configured or synthesized. Same-region
Mantle provider endpoint only; cross-region provider endpoints are excluded.
Model envelopes require actual list-valued `data`; errors, malformed IDs/ownership,
duplicates and known continuation signals fail NotConnected. Only genuine `data: []`
is a valid empty discovery snapshot, never execution success.

## Authorization and bounded snapshots

Source discoverability AND explicit workspace exposure AND user-scoped grants
are required. Requestable means discoverable but lacking a grant; hidden records
never enter ordinary counts/search/details/requests. Native grants are scoped to
user and workspace. Child tools cannot widen parent workspace exposure. A grant
never verifies execution. Detail/version routes reapply authorization.
Metadata cache is in-process, maximum 60 seconds; source changes become visible
after refresh and upstream eventual indexing (not a global 60-second revocation SLA).
User/workspace authorization is reapplied on every projection, even on cache hits. Failed refresh clears stale data. Refresh within
TTL may reuse the bounded snapshot. No stale-on-error. Response revision covers
only the user's visible projection. No tool calls, model calls or Gateway tools/list
transport are implemented here; Registry-declared tools only.

## Verification boundary

Offline SDK contract tests and frontend build are source evidence, not cloud or
E2E acceptance. The audit's 10 CUSTOM / 10 SKILL / 4 AGENT records contain zero
MCP records: the regression must produce **zero tools**, including a CUSTOM
record named `blueprint_mcp_tool_server`. Audit inventory is not an authorization
to read descriptors or reuse old demo data. No actual catalog sample is claimed
until approved sources are configured and their native records are read.

References: AWS AgentCore Developer Guide `registry-supported-record-types.html`,
`registry-searching.html`, `gateway-target-inference-connector.html` (reviewed
2026-09-12); locally installed botocore native service models.

### Source checkpoint results

- Historical pre-fix checkpoint: 141 offline tests passed across native/legacy Catalog authorization, hosted auth,
  callback and email verification suites; native responses validated against the
  installed SDK request and response shapes. Tests use synthetic IDs only.
- Historical pre-fix Cloudscape TypeScript/Vite production build passed.
  This v2 patch changes no UI; no new frontend build is claimed. Existing large-chunk warning
  remains; no claim of browser visual acceptance or live deployment.
- Current unconfigured live state: `NotConnected`, no sample rows. Official
  descriptor examples exist only in offline tests, never as production fallback.
- Audit-only example: `blueprint_mcp_tool_server` remains CUSTOM / Other resources.
  Audit-only `skill_web_browser` remains SKILL, not a native Browser Tool. No
  descriptors from those records were read or copied by this implementation.

Well-Architected source review: **Security** scoped source allowlists, native
consumer discovery and fail-closed authorization; live IAM and endpoint approval
remain unverified. **Reliability** bounded pagination, batch completeness checks,
no stale-on-error. **Performance** bounded cache and batch reads; load behavior
untested. **Cost** bounded metadata reads, zero inference/tool execution.
**Operational excellence** explicit connection/provenance states and offline
contract tests; operator configuration and cloud acceptance remain required.
**Sustainability** bounded refresh/reuse rather than unbounded repeated discovery;
no production utilization claim.

## Native Studio connection release (2026-09-12)

This owner-authorized release supersedes the earlier source-only checkpoint above.
The project owns a new IAM-authorized Registry `governed_agent_builder_catalog`;
no old demo registries, private skill packages or Gateways are reused.
The official AWS Knowledge MCP documentation confirms unauthenticated Streamable HTTP.
An initialize / initialized / tools/list exchange negotiated MCP **2025-03-26** and
returned five operations: `aws___read_documentation`, `aws___search_documentation`,
`aws___list_regions`, `aws___get_regional_availability`, `aws___retrieve_skill`.
No tools/call, skill retrieval, model invocation or Gateway execution was performed.
Server schema is **2025-12-11**, observed server version **1.0.0**. Exact returned
schemas are stored in one native MCP record, version **1.0.0-discovery.20260912**.
Native schema validation and explicit DRAFT → PENDING_APPROVAL → APPROVED admission
precede discoverable List and Batch reads. Record admission does not grant execution.

Exposure is explicitly `research` only, requestable, and bound to the full native
record identity and v2 digest. The source configuration is generated from actual
readback and packaged as `backend/native_catalog_source.json`, enabled only by
`NATIVE_CATALOG_PACKAGED_CONFIG=1`; browser input cannot select this path. This avoids
Lambda's 4 KiB environment limit without adding broad SSM/S3 read authority. The
full configuration is private in ignored `artifacts/native-catalog/source-config-v2.json`.
No fixture models are inserted. `/api/catalog.sources` reports Registry connected,
ModelGateway NotConnected (production model-list permission unapproved), and the
separate platform-owned FoundationLibrary. BedrockClaude/OpenAI execution routes,
CreateInference Deny, ENFORCE and interceptors are not changed.

The narrow release script starts from the existing CloudFormation template and
current Business ZIP; it changes only Business code/config and an additional exact
Registry List / record GetDiscoverableRegistryRecord IAM policy. All other ZIP
members, including QA enrollment and reviewed telemetry, are byte-preserved. Auth,
Authorizer, Worker and model/tool execution flags are untouched. Evaluated changes
must be non-replacing; all physical IDs and outputs are checked after deployment.
Source-status frontend assets use the existing private versioned bucket and CloudFront.

Rollback: ignored `artifacts/native-catalog/rollback-template.json`,
`rollback-business-config.json`, and `Business-rollback.zip` retain the previous
configuration/code. Restore them through another reviewed non-replacing changeset;
revert static index to its previous S3 version. Do not delete DynamoDB/Cognito or
reset identities. The Registry/record tags include project, owner, purpose and
cleanup metadata. On Studio retirement remove Catalog exposure/IAM first, then
explicitly deprecate/delete this project record and Registry; never legacy registries.

Verification: 195 focused native Catalog, Builder, auth/authorization and QA tests
passed; sequential TypeScript/Vite production build passed (existing chunk warning).
Registry-only regression saves genuine-ID/version drafts with no model or Runtime,
keeps approved foundations visible, and leaves unready deployment blocked.
Cloud receipts and final acceptance status are in `docs/NATIVE-CATALOG-RELEASE.md`.

### Finalization status (2026-09-12)

Fresh read-only parent verification confirms `UPDATE_COMPLETE`, the same existing
Studio/account binding, exact current Business ZIP/source/config hashes, preserved
physical IDs/outputs, original role permissions and QA/auth/telemetry bytes and
metadata. Native operator List/Batch and record Get confirm the one APPROVED MCP
record and five observed tools. No pending redeploy or additional AWS writes.
Finalization reran 195 focused offline tests successfully. Five browser tests
remain historical local-auth evidence, not hosted user acceptance. Current public
Catalog rejects anonymous access with HTTP 401; frontend index matches the build.
Operator SDK success does not prove Business-role transport or authenticated
hosted Catalog. ModelGateway remains NotConnected; binding/execution unverified.
Only eight task-owned files are included for fixed-SHA private feature review;
concurrent fault files remain untouched. See [release evidence and limits](NATIVE-CATALOG-RELEASE.md).
No deployed backend source bytes were changed during finalization.
