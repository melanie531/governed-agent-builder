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

- Top-level `approved: true`, `registries: []`, `model_gateways: []`, optional
  `cache_seconds` (0–60; default 30). Empty sources means not connected.
- Each Registry source: `approved: true`, `region`, `registry_id`, `exposure`.
- Each Model Gateway source: `approved: true`, `region`, `gateway_id`,
  `target_ids` (explicit approved targets), `exposure`, `auth: AWS_IAM`, and
  `list_models_url` exactly equal to the documented Gateway HTTPS
  `/inference/v1/models` URL. No redirects, custom hosts or credential URLs.
- Exposure maps canonical IDs to `approved: true`, exact `version`, `workspaces`,
  `requestable`, `owner`, `data_handling`. Registry canonical ID:
  `registry:<registry-id>:<record-id>`; tools append `:tool:<declared-name>`.
  Tool schemas also require the parent exposure's `descriptor_sha256` to match
  SHA-256 of sorted JSON descriptors. Approvers must review safe display metadata
  and schema content at that source version. Descriptors are never returned raw.
- Model IDs: `model:<gateway-id>:<target-id>:<enumerated-model-id>` (no slash in the catalog
  key; the separate `model_id` retains the native target-qualified route).
  Exposure version is SHA-256 of the sorted inference target configuration.
  No automatic wildcard expansion or mapping aliases into concrete models.

There are deliberately no default registry IDs, old demo sources, secret values,
SSM reads, permission changes or account-wide discovery. Approval and source
configuration must be supplied by the resource owner before any cloud discovery.
`client_factory` and `model_reader_factory` are server-side test injection seams.
The IAM model reader resolves normal workload credentials only at a configured
read; this checkpoint has not executed that transport or any cloud read.

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
parent, declared input/output schema safe projection, owner, record version,
descriptor schema version, source hash and unverified execution binding.
External schema refs, defaults/examples, source URLs and raw package content are
not exported or fetched. SKILL.md is discovery metadata only; immutable approved
artifact references and a verified execution binding are required for future
skill execution. This slice marks **all native entries execution-not-ready**.

Models are separate AgentCore Model Gateway inference resources, not Tool Gateway
or Registry rows. Approved exact target GetGatewayTarget reads accept the native
`bedrock-mantle` connector or configured `bedrock-mantle.<region>.api.aws` provider.
The native HTTP GET `/inference/v1/models` is the actual enumeration API (there
is no installed botocore ListModels operation). Only enumerated target-qualified
Bedrock `owned_by=system` Claude/OpenAI routes with exact exposure are admitted.
Gemini/direct external connectors are not configured or synthesized.

## Authorization and bounded snapshots

Source discoverability AND explicit workspace exposure AND user-scoped grants
are required. Requestable means discoverable but lacking a grant; hidden records
never enter ordinary counts/search/details/requests. Native grants are scoped to
user and workspace. Child tools cannot widen parent workspace exposure. A grant
never verifies execution. Detail/version routes reapply authorization.
Metadata cache is in-process, maximum 60 seconds; revocations become visible at
next expiry (not instantaneous). Failed refresh clears stale data. Refresh within
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

- 141 offline tests pass across native/legacy Catalog authorization, hosted auth,
  callback and email verification suites; native responses validated against the
  installed SDK request and response shapes. Tests use synthetic IDs only.
- Cloudscape TypeScript/Vite production build passes. Existing large-chunk warning
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
