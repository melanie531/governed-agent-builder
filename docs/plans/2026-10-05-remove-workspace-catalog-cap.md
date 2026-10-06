# Remove the fixed workspace catalog cap

The portal nests tool rows inside their MCP server. Before this change, the QA
catalog showed nine top-level entries and 26 nested tools, which explains the
35 records counted by the backend. A hardcoded workspace-wide check rejected
publication of another server.

## Required behavior

- An existing catalog with 35 or more entries can accept another approved MCP.
- Keep DynamoDB's native 100-action transaction boundary and revision fence.
- Grant publication and first-login permission initialization use bounded,
  recoverable transactions rather than a catalog-size restriction.
- Preserve workspace isolation, administrator revocations, existing servers,
  saved agents and authentication.
- Do not expose a partially published server. A principal whose initial grants
  are incomplete remains expired until initialization finishes.
- Clean up partial grant preparation through the existing bounded retirement
  path if its registration is explicitly deleted.

## Implementation and verification

1. Add an HTTP publication regression with 35 existing approved entries and
   observe the current capacity rejection.
2. Remove the fixed capacity check and batch grant preparation. Atomically
   insert the server/tools and mark publication READY only after preparation.
3. Exercise publication with many workspace members using the real DynamoStore
   transaction implementation and the existing AWS emulator.
4. Batch initial grants and membership resets. Test large catalogs, interrupted
   initialization, workspace changes and preservation of explicit revocations.
5. Run the affected broader suites, compilation and diff checks.
6. Prepare a scoped backend release, security/tag evidence and rollback guidance.
   Do not merge or overwrite the accepted application without the approval
   required by the supplied AGENTS.md.
7. After authorized deployment, perform publication and the agent tool test
   through the authenticated portal. Backend tests alone are not acceptance.

The current endpoint-display work is separate and remains unfinished. This
change does not require a frontend release. Hosted acceptance publishes the
user's existing upload and creates one temporary agent; cleanup removes only
that temporary agent and preserves the user's MCP setup.

## Progress

- Confirmed the portal's nine visible entries and 26 nested tools.
- Reproduced the HTTP 422 capacity rejection and the DynamoDB transaction
  failures with regression tests before changing production code.
- Removed the cap, batched publication/initialization and added partial
  publication cleanup.
- Passed all 214 affected backend tests, Python compilation and
  `git diff --check`.
- Built a retained-release-based ZIP changing only three backend members.
- Passed the predeployment security/tag audit for 87 resources.
- Prepared release and rollback guidance under
  `artifacts/studio-catalog-cap-removal-20261005/RELEASE.md`.
- The user approved deployment to the existing Studio. CloudFormation reached
  `UPDATE_COMPLETE`, and all six affected Lambda digests match the scoped ZIP.
- Passed the postdeployment security/tag audit for 87 resources.
- Published `snowflake_mcp_self_hosted` and its seven tools through the
  authenticated portal: Research increased from 35 to 43 catalog records.
  Its business-user catalog entry is available and READY.
- Verified every original catalog, agent, version and tracked MCP configuration
  record is unchanged.
- Created and deployed `Catalog capacity QA 20261005` through the portal.
- Completed Snowflake user authorization and verified `test_connection` through
  the agent chat. A fresh read-only `query` call returned `PRODUCT_COUNT=3` and
  query ID `01c784db-0108-cdc8-0011-12b2000b006a`; both portal execution traces
  identify the newly published Gateway target.
- Passed the security/tag audit for 92 resources, including the temporary
  agent's live Runtime.
- Inspected actual browser requests and Chrome DevTools Console. A transient
  authorization-flow HTTP 409 recovered; subsequent flow reads were 200 and
  both tool invocations succeeded. No additional JavaScript error was visible.
- Deleted only the temporary agent through its portal confirmation. The portal
  shows `Agent deleted`; AgentCore returns `ResourceNotFoundException` for its
  Runtime, and its private conversation/deployment records are gone. The
  designed tombstone and deletion audit receipt remain.
- Rechecked all 115 original catalog, agent, version and tracked MCP
  configuration records against their saved hashes: none changed or disappeared.
  Research retains all 43 catalog records, including the user's new connection.
- Restored Platform Admin and left the QA tab on MCP servers. The original
  upload is READY, its OAuth authentication is linked, and the published
  connection is Ready.
- Updated `START-HERE.md` with the completed setup checkpoint, the catalog-count
  explanation, the current query proof and cleanup instructions that preserve
  the user's MCP.
- Hosted acceptance is complete. Evidence and limitations are recorded in
  `artifacts/studio-catalog-cap-removal-20261005/HOSTED-ACCEPTANCE.md`.
