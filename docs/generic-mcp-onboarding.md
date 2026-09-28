# Generic MCP onboarding and management

Use **Platform governance → MCP servers** in Agent Studio. The same form connects
any supported authenticated HTTPS Streamable HTTP MCP endpoint. It registers an
already running server; it does not generate or host MCP server source code.
For Snowflake object creation, use the [Snowflake SQL/PAT runbook](snowflake-managed-mcp.md).

## Register a server

1. Choose **Create MCP connection** and enter its name, description and endpoint.
2. Choose **API key / PAT**, enter the value in the masked field, specify the header
   and optional prefix, then **Save authentication**. Alternatively choose
   **Existing connection** to reuse saved API key, OAuth client credentials or IAM
   authentication. Select the workspaces that should see the connection.
3. Choose **Connect and discover**. The worker creates an AgentCore Gateway target,
   discovers real tool schemas and creates a native AWS Agent Registry MCP draft.
4. Review the schemas and select up to 20 tools. Discovery supports up to 100 tools;
   publication also checks available workspace catalog capacity.
5. Choose **Approve and publish**. The worker checks discovery again, submits and
   approves the Registry record, and publishes the selected tools to the catalog.
6. Wait for **Connection published**. Create an agent in the selected workspace,
   select the connection/tools, deploy, and inspect an actual tool invocation.

The MCP server supplies capabilities. Agent prompts and skills supply the agent's
business logic. Only selected, versioned tool bindings enter the saved manifest.

## Authentication connections

Use **Add authentication connection** to save credentials independently of MCP
onboarding. Enter a name, the endpoint and API key/PAT header details. Snowflake
uses header `Authorization` with prefix `Bearer`.

The masked value goes to a deployment-prefixed Secrets Manager secret. An EXTERNAL
AgentCore Identity provider references it; Gateway injects it into outbound calls.
No credential enters the catalog, browser storage, audit trail or agent prompt.
Credentials bind approved endpoint origins and cannot be embedded in URLs.
The form creates API-key/PAT providers. OAuth and IAM connections must already be
configured by an operator; it does not set up per-user delegated OAuth or Snowflake WIF.

## Edit and delete

1. Select a saved authentication row and choose **Edit authentication**. An empty
   replacement value keeps its current secret; stored values are never displayed.
2. Select an MCP registration and choose **Edit connection**, then **Save and
   rediscover**. Review the new tools and **Approve and publish**.
3. For an unused registration, choose **Delete connection** and type its exact name.
   Studio retires its Registry record, Gateway target, catalog entries and grants.
   The remote MCP server and saved credentials remain.
4. For unused Studio-created credentials, choose **Delete authentication** and
   confirm the exact name. The provider is removed and the secret enters a
   seven-day recovery period.

Saved agent versions protect their MCP dependencies from changes and deletion.
MCP registrations protect the credentials they reference. Studio lists affected
consumers. Deployment-managed credentials are labeled and protected. Create a new
credential and registration when testing a replacement for a connection in use.
Deleting Studio credentials does not revoke a token at the upstream provider.

## Recovery

The browser retains request IDs for onboarding/publication and authentication
setup, never credential values. On an uncertain response, use the displayed status
or reconciliation action before attempting another mutation.

- **Check authentication status** reads the recorded secret/provider state.
- **Continue authentication setup** completes a recorded setup whose provider
  creation has not started. **Retry provider creation** is an explicit action,
  checks native identity first and retains the original provider/secret binding.
- **Reconcile connection** reads recorded native state and queues a continuation;
  it does not blindly repeat an uncertain native write.
- **Retry original request** can explicitly retry Gateway/Registry creation with
  the original native idempotency token. It is capped and cannot retry approval.

Changes carry revisions and retained intent receipts. Deleted records retain audit
history; replaying an old request cannot recreate them. Changed discovery or
credential bindings stop publication and require renewed review.

## Configuration and native services

The server-owned `mcp-onboarding` setting contains `enabled`, `registry_id`,
`registry_arn`, `workspaces`, `secret_arns`, `credential_prefix` and `connections`.
Each configured connection has an `id`, `name`, `allowed_origins` and native
Gateway credential-provider `configuration`. The same configuration is pinned as
`journeyPlatform.mcp_onboarding` in target state for scoped IAM generation.
Self-service credentials are stored separately as `mcp-auth:*` records.

Registry uses **`agent-registry-control`** with `agent-registry` ARNs/IAM actions.
MCP descriptors follow server.json schema 2025-12-11 and actual MCP discovery uses
protocol 2025-03-26. The catalog pins record/version identity, discovery digest and
Gateway binding. Use the [AWS Agent Registry console](https://console.aws.amazon.com/agent-registry/home?region=us-east-1#)
in the deployment account/region. The older Bedrock AgentCore Registry console is
a separate inventory during [AWS's migration](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-faq.html).

Publication does not provide continuous monitoring of native Registry revocation.
Withdraw catalog availability when retiring a connection. Workspace visibility,
agent ownership and upstream data privileges remain separate controls.

See [deployment and rollback](deployment.md). Current acceptance receipts are in
ignored `artifacts/mcp-connection-management/` and subsequent release directories;
version-specific test counts and native IDs belong in those receipts, not in this
operating procedure.
