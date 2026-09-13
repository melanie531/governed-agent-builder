# Platform administrator console

Branch: `feat/platform-admin-console`. Separate worktree from the business journey.

## Intended workflow

1. Business users request missing tools in Tool requests. The same durable
   DynamoDB records arrive in the administrator's Approvals page. Administrators
   move requests through Submitted, In review, Fulfilled or Declined and provide
   a response. Existing capability access requests stay in the AI Catalog and
   arrive in the same administrator workspace. Every decision is audited.
2. Administrators discover Bedrock foundation models and inference profiles,
   register a selected model, review its native metadata, and publish an exact
   version to selected workspaces. Registration alone does not grant access.
3. AgentCore Registry stores versioned resource metadata with native approval
   status. AI Catalog supplies business-facing visibility and execution policy.
   MCP records resolve to the platform's Gateway and its targets; Registry
   metadata must never turn an arbitrary URL into an execution connection.
4. Administrators can withdraw Catalog availability, manage grants and approved
   Foundation versions, and review the audit trail. Existing agents revalidate
   Catalog approval and versions before execution.
5. An overview shows deployed agent inventory and pending approvals. Performance
   distinguishes invocation outcomes and end-to-end latency from model inference
   latency, token usage and provider errors. Aggregate operational metadata does
   not expose prompts, datasets, conversation text, credentials or raw traces.
6. Cost Explorer reports project-tagged AWS costs by service and date, with the
   account, time window, currency, data freshness and estimated billing flag.
   Unavailable/inactive cost allocation or absent metrics display as unavailable,
   not zero. Model account-level metrics are labelled with their broader scope.

## Implementation sequence

- Complete and bring in the short-tool-name/DynamoDB status fix.
- Add authenticated administrator APIs and independent English UI components,
  reusing existing approvals, grants and audit storage.
- Add explicit native Registry configuration and validated model discovery and
  publication. Pin record/version/binding provenance; keep Gateway execution.
- Add read-only CloudWatch and Cost Explorer adapters with bounded queries,
  caching and partial-source error states. Scope IAM to this platform where the
  service supports resource permissions.
- Test role isolation, stale decisions, publication/revocation, durable requests,
  metric denominators and missing costs. Exercise the admin/business handoff in
  browsers, then verify the hosted AWS integration.

## Research findings and sources

- [AgentCore CreateRegistry](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CreateRegistry.html):
  Registry supports explicit approval; its consumer authorizer does not replace
  IAM for administrator CRUD APIs.
- [CreateRegistryRecord](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CreateRegistryRecord.html):
  native descriptor types are MCP, A2A, AGENT_SKILLS and CUSTOM. A model is
  represented by a CUSTOM descriptor with a platform-defined, validated schema.
  Record creation is asynchronous and must not be shown as approved on HTTP 202.
- [UpdateRegistryRecordStatus](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_UpdateRegistryRecordStatus.html):
  status decisions include a required reason. Native approval is separate from
  workspace publication and user grants.
- [Bedrock monitoring](https://docs.aws.amazon.com/bedrock/latest/userguide/monitoring.html):
  Converse uses the bedrock-runtime monitoring surface.
- [Cost Explorer filtering](https://docs.aws.amazon.com/cost-management/latest/userguide/ce-filtering.html):
  linked-account and cost-allocation-tag filters are necessary to attribute
  platform costs; arbitrary account totals cannot be called platform costs.

Acceptance requires real native readback where configured. Offline fixtures are
labelled synthetic and never represented as production observations.

## Implemented boundaries

- Administrator endpoints live under `/api/admin/platform`. Existing Catalog
  access requests and new tool requests retain their own durable APIs. Decisions,
  publication, withdrawal, model validation and evaluation policy changes write
  audit records. Optimistic versions reject stale decisions, including an
  administrator draft that remained open during background refresh.
- Registration creates an unpublished Catalog model. A bounded native Converse
  call checks the model connection and tool-configuration support. Native
  Registry approval precedes Catalog publication, and publication does not grant
  workspace-wide access automatically.
- Platform resource descriptors currently use native Registry `CUSTOM` records
  with the validated `gab.catalog.v1` schema. MCP descriptors refer to the
  existing platform Gateway/target; they are not represented as arbitrary native
  MCP `server.json` records or arbitrary executable URLs.
- Agent latency uses successful Runtime receipts; error rate excludes pending
  and unknown outcomes and shows their counts separately. Model p50/p95 query
  one complete UTC day from CloudWatch without averaging percentiles.
- Cost reporting uses `UnblendedCost`, account and project-tag filters, and a
  one-hour in-process cache. Untagged inference/shared costs remain unallocated.
- Evaluation policy is checked before Journey actions. Absent datasets continue
  to skip evaluation. Deployment tooling preserves administrator-managed Catalog
  decisions and stops if an operator attempts to replace a reviewed binding.

## Account verification

Target: `820242898417`, `us-west-2`, profile `account-820`.

On 2026-09-13, the real `CreateRegistry` call returned `AccessDeniedException`.
The existing operator role has `AdministratorAccess` and IAM simulation returned
`allowed`; the actual service call is the authority. No broader IAM role was
created to work around this. Native Registry publication stays unavailable until
the account-side restriction is resolved. Other administrator integrations can
be deployed independently.

The existing `project` cost allocation tag was inactive. Activation was requested
for that tag only. Billing attribution is delayed and no historical tag backfill
or account-wide billing changes were requested.

Provision/reconcile with `scripts/platform_admin_configure.py` using the same
explicit account/profile/region/release-state flags as the Journey deployment.
The hosted acceptance runner is `frontend/journey-e2e/admin-live.mjs`; it verifies
real Cognito role isolation, business/admin request handoff, independent DDB/audit
readback and native monitoring sources. It records Registry unavailability as
such and does not call that a successful native publication.
