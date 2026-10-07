# Snowflake-managed MCP with user OAuth

## Objective

Add and document a Snowflake-managed MCP pattern using per-user OAuth 3LO
instead of a shared PAT. Prove the actual Studio journey in the default AWS
account first, then repeat it in the burner account. The server remains hosted
by Snowflake; this pattern does not require uploading the Python MCP package.

Work on `feat/snowflake-managed-mcp-3lo`, based on merged PR #3. Preserve existing
PAT registrations, successful Python MCP resources, and unrelated staged files.
Use separate, clearly named validation connections and retain successful results
for user verification. Only failed resources created for this work may be removed.

## Tasks and gates

| Task | Deliverable | Status |
| --- | --- | --- |
| T0 | Confirm branch, account bindings, existing managed MCP setup, and browser profile | Complete; default Studio sign-in verified |
| T1 | Verify Snowflake OAuth and managed MCP contracts; design the smallest compatible configuration | Complete; existing native Gateway 3LO path selected |
| T2 | Add regression coverage and repair any required Studio support; run affected suites/builds | Native MCP `isError` regression reproduced and fixed; 354 affected tests pass; pinned package tests pass |
| T3 | Write the generic managed MCP OAuth setup and user test journey | Complete; setup and Chat instructions exercised in both environments |
| T4 | Preflight/audit default account; create and test the entire journey through Studio and Snowflake UI | Complete; consent, identity, table list, row count, controlled tool error and reload verified; retained agent v3 |
| T5 | After T4 passes, repeat the entire journey in the burner account | Complete; separate OAuth integration, connection, business agent, identity, tables, count and reload verified |
| T6 | Final security/tag checks, resource inventory, sanitized evidence, and documentation verification | Complete; environment audits pass for 92 default and 69 burner resources; both exact runtimes and native OAuth bindings pass; generic guide, companion schema, compilation and whitespace verified |

T1 distinguishes native Snowflake-managed MCP from the Python runtime example,
and confirms the OAuth client, scopes, role grants, endpoint, discovery behavior,
and callback requirements from current source and official documentation.

T2 follows test-driven development for each behavior defect: reproduce with the
smallest meaningful failing contract test, implement the minimum repair, rerun
the focused test, then run the broader affected suites and build. If existing
support is sufficient, record that conclusion rather than inventing new layers.

## Hosted acceptance

For each account, the flow under test is:

1. Prepare a dedicated Snowflake OAuth integration and narrowly scoped reader
   role for the Snowflake-managed MCP endpoint.
2. In Studio's platform view, connect the existing hosted endpoint and configure
   **User sign-in (OAuth 3LO)**. Register the generated callback for that account.
3. Review discovered tools and publish the intended capabilities.
4. In the Business view, create and deploy an agent with the intended tools.
5. Submit a real user question through Chat. Complete Snowflake consent in the
   default Chrome profile when required.
6. Verify the rendered result, intended Gateway tool call, and data/query
   evidence. Confirm the server authorizes the user's reader role.
7. Reload the page and verify saved results. Inspect actual browser requests,
   console/page errors, and screenshots.
8. Verify security, private access, and `auto-delete=no` on supported resources.

Use a small synthetic dataset or existing explicitly authorized demo data.
Discovery, a READY runtime, backend invocations, or a model-generated answer
alone do not satisfy hosted acceptance. Snowflake credentials and passkeys remain
in the user's default browser; secrets, tokens, cookies, and authorization URLs
must not appear in evidence.

## Scope and release controls

- Default account acceptance must pass before burner provisioning or tests.
- Confirm current STS account/region and deployment state before AWS writes.
- Use existing deployment boundaries and scoped release scripts if code changes
  are needed; no merge or production cutover is authorized by this task.
- Keep authentication methods separate. Do not repurpose an existing shared PAT
  secret or OAuth client that belongs to a different retained pattern.
- Do not change runtime execution limits, session architecture, or unrelated
  agent behavior to implement this authentication pattern.
- Use the existing default Chrome profile and the rendered portal for creation
  and acceptance. Read-only backend diagnostics may explain a failure but do
  not replace the user's journey.
- Journal each intended cloud or Snowflake change before execution; reconcile
  uncertain outcomes before retrying.

## Evidence and final handoff

Sanitized evidence is stored under the Git-ignored path
`artifacts/managed-snowflake-mcp-3lo-20261007/`, with separate `default/` and
`burnner/` receipts. Documentation uses generic placeholders and explains how
users obtain every field, including the generated OAuth callback.

The final handoff must include the retained resource names, both environment
results, exact test prompts and expected outputs, changes made, validation
coverage, known limitations, and a numbered user verification journey.

## Progress: branch and contract verification

- PR #3 is merged. The new branch starts at
  `9a80e39fb26aa04c46cc67fdbaec62effa4c5b4a`. The five pre-existing staged
  `.DS_Store` files remain untouched.
- Current default-profile STS identity matches the default-account release state.
  The existing deployment's security/private-access/tag audit passes 90 checks.
  Its historical `DELETED` package receipts are not active deployment failures.
- Current official Snowflake documentation explicitly supports Snowflake OAuth
  for managed MCP servers, a confidential client, explicit role scopes, and
  disabled secondary roles. No external IdP or uploaded Runtime is required.
- The existing Studio Gateway path accepts generic HTTPS endpoints with
  `AUTHORIZATION_CODE` credentials and an inline MCP tool schema.
  `DESCRIBE MCP SERVER` returns `server_spec`, not `tools/list` input schemas.
- The companion `query_sql` descriptor follows the prior actual managed-server
  discovery contract (`sql` is the argument). Current live compatibility remains
  a T4/T5 acceptance requirement.
- The relevant existing OAuth provider, Gateway, user connection and transport
  suites pass: 64 tests, with two existing dependency deprecation warnings.
- The initial draft described the managed MCP OAuth pattern separately. It is
  now part of the single [Snowflake setup guide](../snowflake-setup.md), linked
  from the README and `start-here.md`.
- A dedicated Snowsight worksheet was created. The three existing managed MCP
  servers were inventoried and preserved. New database, schema, two-row sample
  table, reader role, managed MCP server and disabled OAuth integration were
  created through that worksheet after checking their names were unused.
- Snowsight results verify the read-only server specification, five narrowly
  scoped reader grants and sample count of two. The user's existing default
  warehouse was already suitable; neither the default warehouse nor default
  role was changed.
- Default Studio sign-in is verified. The native OAuth provider request was
  submitted through **Save OAuth connection**, with its secret transferred
  directly between the visible setup forms and never written to local evidence.
  Its generated callback was copied back into the new Snowflake integration,
  verified and enabled.
- **Connect and review**, explicit selection of `query_sql`, and **Approve and
  publish** completed through Studio. The new connection is **Ready**, and the
  portal confirms **Connection published**.
- In Business User view, a new Knowledge Q&A agent was created and deployed.
  Its inherited shared PAT connection and schema-discovery skill were removed
  from that agent; the review page confirms one allowed `query_sql` tool on the
  new managed 3LO connection. The guide now describes these selections.
- A real Chat identity request reached the Snowflake sign-in page through
  **Open provider sign-in** in the default Chrome profile. Human Snowflake
  sign-in did not complete before the request expired. The expired tab created
  by this test was closed. The saved connection and agent were retained and
  later tested using a fresh authorization request.
- The default post-provisioning audit passes for 92 resources. Additional
  read-only checks verify the new Runtime, endpoint, identity and logs, plus
  the Cognito-protected OAuth Gateway and exact native target/provider/scopes.
  Browser observations show no JavaScript console/page errors through the
  consent gate; one transient agent-read HTTP 409 was followed by successful
  reads.
- Documentation checks pass for the companion schema, 28 local links, absence
  of environment-specific values in the new guide, and whitespace. These checks
  were repeated after both hosted journeys passed.
- The user completed a fresh Snowflake consent and submitted a question about
  an older database. Snowflake query history confirms the new reader role
  reached the query and received permission error `2003`. That role is scoped
  to this example's new sample database; its grants have not been broadened.
- The native MCP returned `isError: true`, which the harness incorrectly raised
  as an uncaught transport failure. A regression through the real Gateway
  transport seam reproduced that crash. The repair records a sanitized tool
  error, then uses the existing final-answer turn without further tool calls.
  Authorization challenges and transport failures keep their existing handling.
- The new tests cover both ordinary tools and tools with response adapters.
  All 354 affected journey/MCP tests pass. The release ZIP changes only the two
  runtime source members involved in this repair, preserving the pinned
  dependencies and deployed parser; its 28 runtime/transport tests also pass.
- A new `SELECT 1 AS CONNECTION_CHECK` request submitted through Chat returned
  `1` with exactly one `query_sql` call. This proves consent and tool invocation,
  but does not replace the identity, permitted-data and denied-query tests.
- Default-account Chat tests returned the intended user/reader role/warehouse,
  no active secondary roles, the one sample table and a row count of two.
  Each successful question used one Gateway tool call. The returned count query
  ID matches Snowsight Query History.
- Hosted testing exposed an inaccurate model explanation of the sanitized
  error. The error record now explicitly marks its cause and downstream
  execution as unverified. A fresh denied-query test on v3 shows one error-marked
  tool call and the controlled UI warning, without `RuntimeClientError` or
  invented successful data. A subsequent permitted query succeeds.
- The final default runtime is pinned to the reviewed artifact, has IAM inbound
  authorization, and passes all 12 readiness, artifact, tag and log checks.
  The saved Chat result and execution details survive a page reload.
- After default acceptance passed, the burner preflight passed for 67 resources.
  A separate Snowflake OAuth integration was created through Snowsight. Its
  client credentials were entered through Studio, and its actual generated
  callback was registered and verified before testing. The native MCP server
  and sample data are shared; the OAuth integrations are independent.
- Burner **Connect and review**, explicit `query_sql` selection and **Approve
  and publish** completed through the portal. In Business User view, a new
  agent was configured with only that connection and deployed through Studio.
- Burner Chat returned the intended user, reader role, no secondary roles and
  warehouse; the sample table name; and a count of two. Each question made one
  actual `query_sql` call. Snowsight Query History confirms all three queries
  succeeded under the reader role, including the count's returned query ID.
- Burner Chat results and execution details survive reload. Browser observations
  show no console errors, page errors or unhandled rejections. A transient read
  HTTP 409 recovered with successful reads and did not prevent persistence.
- The final burner environment audit passes for 69 resources. Its exact agent
  runtime passes 12 artifact, readiness, authorization, tag and log checks. The
  native OAuth Gateway passes 15 checks covering its Cognito boundary, target,
  scopes, callback, tool schema, provider and deployment-prefixed tagged secret.
- Successful examples are retained as **Managed Snowflake Reader Default
  20261007** (v3) and **Managed Snowflake Reader Burner 20261007** (v1), with
  matching **Managed Snowflake 3LO** connections. No pre-existing Snowflake
  integration, PAT registration, Python MCP deployment or user agent was deleted.
- `acceptance.md`, `acceptance-state.json` and `resource-journal.json` in the
  evidence directory index the final resources, tests, releases and audits.

## Runtime release and rollback

The release changes only native MCP tool-error handling in
`foundation_harness/journey_mcp.py` and `foundation_harness/journey_runtime.py`.
It preserves the retained package's dependencies and transport parser. No
execution limits, session architecture, OAuth grants or endpoint configuration
were changed by this repair.

The account-bound release receipts retain the previous and replacement artifact
hashes, immutable S3 versions, activation intent and verification. The Business
Lambda was recycled with its existing code to reload the platform setting.
Existing deployed agent versions remain pinned to their own artifacts. An
operator rollback should restore the recorded previous platform artifact and
explicitly deploy a chosen agent version through Studio, then repeat its Chat
check. Do not overwrite a deployed runtime or discard the retained receipts.

## Follow-up: guided MCP connection flow

The onboarding follow-up replaces the combined form with **Server →
Authentication → Tools and workspaces → Review and publish**. Hosted endpoints,
complete ZIP uploads and ready uploaded packages share this flow. Authentication
is saved and selected within it; separate authentication and deployment
management are expandable administration sections.

Hosted 3LO accepts the server owner's tool-definition JSON file, previews its
tools and blocks empty or invalid definitions before registration. Uploaded
packages retain their discovered definitions. This does not add automatic
discovery before consent for hosted 3LO.

The complete frontend suite passed 112 tests. After the last layout adjustment,
the build and all 37 affected MCP tests passed. Desktop/mobile views and 88
documentation links/anchors were checked. The consolidated
[Snowflake setup guide](../snowflake-setup.md) now follows the wizard, including
the different callback lookup for an existing AWS provider reference.

Only the burner frontend assets were published for this follow-up. In default
Chrome, a new owned Snowflake server and **MCP Flow QA 20261008** connection were
created, the real JSON file was selected through the native file picker, and
`query_sql` was published. The existing OAuth integration and its already-allowed
callback were reused without modification.

In Business User view, **MCP Flow Reader QA 20261008** was created and deployed.
An initial consent completion remained unresolved; its evidence is retained.
After a fresh user sign-in, the identity query and two-row fixture count
succeeded, each with one `query_sql` call. Snowsight Query History independently
confirmed both queries under the reader role, and Chat results survived reload.
One transient flow-status HTTP 409 was followed by successful reads; observed
JavaScript console/page error and unhandled-rejection counts were zero.

The post-deployment audit passed for 73 resources, and the new agent passed all
12 runtime/artifact/access/tag/log checks. Successful resources remain available.
No runtime or consent implementation was changed by this UI follow-up. Evidence,
release/rollback receipts and remaining verification limits are indexed in
`artifacts/mcp-connection-flow-20261007/acceptance.md`.

## Documentation follow-up: first-time setup with a new provider

The canonical [Snowflake setup guide](../snowflake-setup.md) now makes a new
managed MCP and new OAuth provider the complete B1–B10 walkthrough. It covers
Snowflake data and grants, server creation and endpoint construction, a new
disabled OAuth integration, the commands supplying each form value, Studio's
**Create provider** fields, generated callback registration and enablement,
tool definitions, publication, and Business User agent tests.

The field table maps all nine controls shown in the Authentication step to a
selection, a Snowflake result column or the client-secret function. The guide
explains that Studio changes the selector to **Existing connection** after
saving the newly created provider. Existing-provider reuse is an optional
section after the first-time walkthrough. PAT and Python ZIP setup remain
separate paths in the same file; `start-here.md` links directly to the new path.

This follow-up changes documentation only. Validation checks the current UI
labels, current official Snowflake OAuth/MCP references, local links and
anchors, JSON/example syntax, and the correspondence between the inline tool
definition and its companion file. Evidence and the scoped before/after
documents are under
`artifacts/mcp-connection-flow-20261007/docs-new-provider-20261008/`.
No new integration, provider, registration or agent was created for this
documentation revision, and no fresh hosted journey is claimed.

## Documentation follow-up: actual tool definitions

B7 now identifies the authenticated Snowflake MCP `tools/list` response as the
source of tool schemas and describes the endpoint, headers, request body,
response export, event-stream decoding and pagination. It distinguishes the
required user access token from the OAuth application's client secret. The
one-tool example is explicitly limited to its matching server.

The guide also records the unresolved product gap: hosted 3LO requires a schema
before publication and provides no pre-registration consent/discovery action.
A new user with only the saved Studio OAuth configuration cannot retrieve an
arbitrary server's schema entirely through the portal. External-client export
is documented as an operator path, not as completed Studio UI acceptance.
No authentication flow or deployment was changed in this follow-up. Evidence
is under `artifacts/mcp-connection-flow-20261007/docs-tool-discovery-20261008/`.

## Documentation correction: use the queried SQL server definition

The user demonstrated that `DESCRIBE MCP SERVER` returns a JSON `server_spec`.
B2 and B7 now describe that result accurately and distinguish it from the MCP
input schema required by Studio. For the known `SYSTEM_EXECUTE_SQL` tool, B7
maps the returned name and description to the existing SQL input contract and
provides the JSON directly beside the paste/import instructions. The warehouse
and read-only settings remain server configuration.

This path requires no additional PAT or external OAuth client. The separate
token-export helper under consideration was abandoned before implementation;
its new failing tests were removed. No authentication, application behavior or
cloud resource was changed. Other tool types still require their own input
schemas; the guide does not claim that arbitrary Snowflake server specifications
can be imported directly. Validation evidence for this correction is under
`artifacts/mcp-connection-flow-20261007/tools-export-20261008/`.

## Follow-up: import Snowflake server_spec directly

The user requested that Studio accept the unedited JSON returned by
`DESCRIBE MCP SERVER`. The earlier instructions requiring the user to supply
`inputSchema` are superseded by application conversion.

The scoped implementation adds version-1 `SYSTEM_EXECUTE_SQL` conversion to
the existing tool-definition importer. Standard MCP schemas remain accepted.
Names and descriptions are preserved; Snowflake warehouse/read-only settings
remain on the server. Unknown tool types and invalid definitions block the
whole import rather than producing incomplete registrations.

The implementation plan, initial failing UI regression, build/test results and
hosted acceptance evidence are recorded under
`artifacts/snowflake-server-spec-import-20261008/`. The canonical B7 walkthrough
now follows **DESCRIBE → copy server_spec → paste in Studio → review**.
The release scope is the existing isolated burner frontend asset bundle.

The regression first failed on the user's exact unconverted JSON. The final
build and all 114 frontend tests pass, including pasted and file-based imports,
unsupported types/versions, generic MCP schemas and responsive layouts.
Documentation links/examples and `git diff --check` pass.

In the default Chrome profile, a dedicated Snowflake test server was created
through Snowsight and its real `server_spec` result was pasted unchanged into
the deployed burner Studio. The preview confirmed one SQL tool. The subsequent
Review page displayed the generated `sql` input schema returned by the backend.
Publication reached **Connection published / Ready** and survived reload.
The retained connection is **Snowflake Spec Import QA 20261008**.

An initial attempt used authentication bound to a different server endpoint and
received HTTP 422. That failure is retained in the evidence. A new endpoint-bound
reference to the same existing AWS OAuth provider resolved it; no Snowflake
OAuth integration or runtime code was changed. The final security/tag audit
passes for 76 resources, and the versioned frontend assets and CloudFront
invalidation are verified. This follow-up verified import and publication;
it did not repeat agent creation, user consent or a live agent query.
