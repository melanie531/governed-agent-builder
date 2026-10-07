# Generic MCP onboarding and management

For the portal walkthrough, start with the
[Studio user guide](start-here.md). It covers server setup, each authentication
method, publication, agent creation and tool testing. The sections below also
cover package implementation and platform administration.
For Snowflake, use the single [Snowflake setup guide](snowflake-setup.md).
It covers managed MCP with PAT or user OAuth, Python package upload, credential
retrieval, callback checks and agent testing.

Use **Platform governance → MCP servers** in Agent Studio. The same form connects
any supported authenticated HTTPS Streamable HTTP MCP endpoint. **Add MCP
connection** opens **Server → Authentication → Tools and workspaces → Review and
publish**. In **Server**, choose **Connect hosted endpoint**, **Upload MCP package
(.zip)** or **Use a deployed package**. Complete packages contain their own
code, non-secret provider configuration and dependencies. The upload form has
no vendor profile, account, warehouse, role or authentication-flow fields.
Authentication is configured after the uploaded server is ready.

A fresh deployment has no saved authentication or MCP registrations. You supply
the server URL and credentials; the platform does not install a provider example
or connect to the repository author's accounts. Models are published separately
through model administration. See [first installation](deployment.md#first-installation).

## Register a server

1. Choose **Add MCP connection → Connect hosted endpoint** and enter its name,
   description and endpoint. For your own server package, follow **Upload a
   complete MCP package** below. Choose **Next**.
2. In **Authentication**, choose **API key / PAT**, enter the value in the masked field, specify the header
   and optional prefix, then **Save authentication**. Alternatively choose
   **AWS IAM / AgentCore Runtime** for a Runtime endpoint in the platform's account
   and region, then **Save IAM connection**; this requires no secret. Choose
   **Existing connection** to reuse saved API key, user OAuth, service OAuth or IAM
   authentication. After saving or selecting authentication, choose **Next**.
   OAuth has two separate setup steps: **User sign-in (OAuth 3LO)** for each
   user's authorization, and **Service credentials (OAuth 2LO)** for machine
   access. Follow the corresponding instructions below.
3. In **Tools and workspaces**, select the workspaces that should see the connection.
   Choose **Connect and discover**. The worker creates an AgentCore Gateway target,
   discovers real tool schemas and creates a native AWS Agent Registry MCP draft.
    For per-user OAuth, import the server owner's `tools/list` JSON file using
    **Choose tool definitions file**, inspect the preview, then choose
    **Connect and review**. For Snowflake SQL servers, paste the unedited
    `server_spec` returned by `DESCRIBE MCP SERVER`; Studio prepares the inputs.
    See the [Snowflake import steps](snowflake-setup.md#b7-load-the-tool-definition-and-choose-workspaces).
    An uploaded package supplies these definitions
   automatically. The native Gateway target uses the supplied schemas
   without acquiring an administrator's provider token.
4. In **Review and publish**, inspect the schemas and select up to 20 tools.
   Discovery supports up to 100 tools.
5. Choose **Approve and publish**. The worker checks discovery again, submits and
   approves the Registry record, and publishes the selected tools to the catalog.
6. Wait for **Connection published**. Create an agent in the selected workspace,
   select the connection/tools, deploy, and inspect an actual tool invocation.

The MCP server supplies capabilities. Agent prompts and skills supply the agent's
business logic. Only selected, versioned tool bindings enter the saved manifest.

There is no fixed 35-entry workspace catalog cap. **Registry & AI Catalog** shows
each MCP server as one row; select it and expand **Tools exposed by this MCP
connection** to see its individual tools. They are separate catalog records.
Publication prepares workspace permissions in bounded batches, then makes the
server and selected tools available together.

## Upload a complete MCP package

Use this path for a new MCP with its own implementation. Upload a **deployable
ZIP**, including supporting modules, resource files and bundled third-party
dependencies. The ZIP must have `main.py` at its root, without a containing
project directory. Studio does not add provider code or install requirements.

Build dependencies for **Python 3.13, Linux ARM64**. The server must expose MCP
Streamable HTTP on `0.0.0.0:8000/mcp`. The limits are 64 MiB compressed, 256 MiB
expanded and 20,000 archive entries. ZIPs with unsafe paths, duplicate entries,
links, incompatible native libraries or invalid `main.py` syntax are rejected
before a Runtime is created.

1. Build the [complete generic example](../examples/complete-mcp-package/README.md)
   or adapt its source and dependency lock. For Snowflake, follow
   [Python package setup](snowflake-setup.md#c-uploaded-python-mcp-with-user-sign-in-3lo)
   in the Snowflake guide.
2. Open **MCP servers → Add MCP connection → Upload MCP package (.zip)**.
   Enter a connection name and choose the built ZIP.
3. Click **Upload and deploy package**. Studio transfers bounded chunks through
   the authenticated API into private, encrypted S3 storage, freezes their
   versions, validates the archive, deploys the same ZIP bytes and discovers the
   real tool schema.
4. If transfer stops, click **Check package status**, select the same ZIP if
   necessary, and click **Resume same package**. This retains the request and
   skips confirmed parts. It does not create another deployment.
5. Wait for **READY**, then click **Use this MCP package** and **Next**.
   Studio carries the endpoint and discovered tools into **Authentication** and
   **Tools and workspaces**. If the package was already deployed, start with
   **Use a deployed package** and select it from **Saved MCP package deployments**.
   For the generic greeting example, select
   **AWS IAM / AgentCore Runtime**, then **Save IAM connection**. The configured Studio Gateway already has a
   scoped invocation grant for these packages. External data-source permissions
   are configured separately.
6. For packages requiring user OAuth, select **Authentication method → User
   sign-in (OAuth 3LO)** and follow [User sign-in](#user-sign-in-oauth-3lo).
   The [Snowflake guide](snowflake-setup.md#configure-user-sign-in) provides that
   provider's exact setup. Check the generated callback against its allowed URLs;
   add it only if missing. Wait for **Authentication saved**; Studio selects the
   new saved connection automatically.
   An existing saved OAuth connection is pinned to its exact endpoint;
   it cannot be reused for another uploaded server merely because the hostname
   matches.
   Service OAuth 2LO is a separate path for providers that support client
   credentials. A package upload does not perform either OAuth flow.
7. Choose **Next**. In **Tools and workspaces**, select workspaces and connect.
   In **Review and publish**, select tools, then choose **Approve and publish**.
   The workspace needs one catalog entry for the connection plus one
   for each selected tool.
8. Create and deploy an agent with those published tools. For the generic
   example, ask it to call `package_greeting` with `name: "Studio QA"` and verify
   `Hello, Studio QA!` and `complete-package-v1` in the actual tool result.
   For Snowflake, complete user consent, then start with `test_connection`,
   `list_tables`, `describe_table` and a bounded read-only query.

The example's ZIP includes its support module and text resource, so that tool
result checks more than the entry point. A folder containing only `main.py` and
`requirements.txt` is not a deployable package unless its imports are already
included.

If the Runtime deploys but tool discovery fails, check its logs and the startup
configuration inside the package. Select **Choose another package** to upload a
corrected ZIP; reconciliation repeats discovery with the same immutable
configuration. Find status, copyable endpoints and deletion controls under
**Manage uploaded MCP servers → Uploaded MCP deployments**.

### Packages that accept provider bearer tokens

A package can declare a provider-neutral authentication contract in the ZIP-root
`mcp-package.json`:

```json
{"schema_version": 1, "bearer_validation_tool": "_studio_validate_token"}
```

The named MCP tool must have no arguments and declare `readOnlyHint: true`.
It validates a token received in
`X-Amzn-Bedrock-AgentCore-Runtime-Custom-Access-Token`, returning an MCP
`structuredContent` object exactly `{"authorized": true}` or
`{"authorized": false}`. In FastMCP, use a typed Pydantic return model with an
`authorized: bool` field. A JSON string in text content is not authorization.
Missing credentials, provider failures and invalid responses must deny access.
Never acquire credentials or execute a caller's business query in this check.

The shared bridge checks the package's owned READY Runtime and pinned version,
invokes this validation tool privately with AWS IAM, and forwards a request only
after validation succeeds. Tokens remain in request headers, outside tool
arguments, logs and stored package metadata. This tool is omitted from Studio's
business-tool inventory. The Snowflake example implements this contract; all
Snowflake settings and token-validation logic reside in its package.

Packages without this manifest use IAM access. Packages with the manifest also
offer the authenticated bearer endpoint for API keys/PATs and compatible OAuth
providers. Configure either OAuth flow separately after deployment; package
upload does not save a provider secret or grant consent.

Operators bind package permissions using `scripts.mcp_onboarding_deploy.py
configure_packages` with the existing explicit account, region, profile and
release-state arguments, then deploy the scoped CDK-generated policies before
publishing the UI. Generic-only hosting can use a `mcp-python-config` with
`package_bucket`, `runtime_role`, `runtime_prefix` and `deployment_prefix`; an
installed Snowflake bundle is not required by the package API.

## Authentication connections

The **Authentication** step saves and attaches credentials during onboarding.
For separate credential administration, expand **Manage saved authentication**
on the overview and use **Add authentication connection**. Enter a name, the
endpoint and API key/PAT header details. Snowflake
uses header `Authorization` with prefix `Bearer`.

The masked value goes to a deployment-prefixed Secrets Manager secret. An EXTERNAL
AgentCore Identity provider references it; Gateway injects it into outbound calls.
No credential enters the catalog, browser storage, audit trail or agent prompt.
Credentials bind approved endpoint origins and cannot be embedded in URLs.
The form creates API-key/PAT or OAuth providers, or saves a secretless **AWS IAM /
AgentCore Runtime** connection. The two OAuth choices use the separate setup
steps described below. Studio stores a new client secret in Secrets Manager and
creates an EXTERNAL AgentCore OAuth provider.

### User sign-in (OAuth 3LO)

1. In **Add MCP connection**, enter a name and endpoint, or use **Use this MCP
   package** to fill them from a READY upload. Choose **Next** to open
   **Authentication**. In **Authentication method**,
   select **User sign-in (OAuth 3LO)**. The form displays **Set up user sign-in
   (3LO)**. For uploaded packages that accept provider tokens, Studio chooses
   their bearer-authenticated HTTPS endpoint when you select this method.
2. In **OAuth provider setup**, select the option that matches your OAuth client:

   | Option | Fields to supply |
   |---|---|
   | Existing provider | **OAuth provider ARN** and **OAuth scopes**, separated by spaces. This reuses an installed provider and secret. |
   | Create provider | **OAuth client ID**, **OAuth issuer**, **Authorization endpoint**, **Token endpoint**, **Client authentication**, **OAuth client secret** and **OAuth scopes**. The client must support authorization-code grants. |

3. Click **Save OAuth connection**. For a pending request, use **Check OAuth
   connection status** and **Continue OAuth setup** if offered. Expected:
   **Authentication saved**, with **Authentication method** changed to
   **Existing connection** and the new `<connection name> OAuth` selected.
4. Check the displayed AgentCore callback against the upstream client's allowed
   URLs. If that exact URL is already allowed, make no change. Add it only if
   missing, preserving existing callbacks. Choose **Next**.
5. In **Tools and workspaces**, review the definitions loaded from your package,
   or import the hosted server's JSON using **Choose tool definitions file**.
   The file can contain `{"tools":[...]}` or a JSON-RPC response with
   `result.tools`; it must fit 180 KB and contain 1–100 tools with unique names
   and object input schemas. Studio also accepts version-1 Snowflake
   `server_spec` JSON for `SYSTEM_EXECUTE_SQL` tools and supplies their input
   schemas automatically. **Paste tool definitions instead** accepts the same
   formats.
   Select **Visible workspaces**, then **Connect and review**.
   In **Review and publish**, select the tools,
   choose **Approve and publish**, and wait for **Connection published**.
   Each user completes provider sign-in and consent when their agent calls a tool.

**Existing provider** is a choice inside OAuth setup. **Existing connection**
selects authentication that has already been saved for the endpoint. Saved
authentication can be restricted to one exact endpoint. For a different server
URL, choose **Existing provider** inside OAuth setup to save a new reference
for that endpoint, even when both servers reuse the same OAuth provider.

If authentication was saved through the optional management dialog, start
**Add MCP connection**, select the server and choose **Next**. In
**Authentication**, select **Existing connection** and the reference saved for
that exact endpoint. No new OAuth setup is needed.

**Existing provider** saves a reference to an existing deployment-prefixed 3LO
provider backed by an EXTERNAL Secrets Manager secret. The platform's user Gateway
accepts Cognito JWTs, checks current Studio grants, and uses native
`AUTHORIZATION_CODE` outbound authentication. When a tool needs consent, the
agent page displays **Continue to provider**. The callback completes under the
same Cognito user. A fresh saved question can continue once when no tool has
completed and its agent version has not changed. Otherwise, Studio requires a
new explicit invocation. Studio login remains Cognito.

### Service credentials (OAuth 2LO)

1. In **Authentication**, select **Service credentials (OAuth 2LO)**. The form displays **Set up service
   access (2LO)**.
2. Enter the client ID, HTTPS **OAuth discovery URL**, client authentication
   method, masked client secret and scopes. The discovery document supplies the
   provider's token endpoint and authentication metadata.
3. Choose **Save OAuth connection**, then **Next**. Select workspaces in
   **Tools and workspaces**, then **Connect and discover**. Review and
   publish the discovered tools. This path has no end-user consent or callback
   registration step.

2LO uses the machine Gateway and the provider's `client_credentials` grant. The
provider must support that grant and the MCP must accept the resulting token.
Native Snowflake OAuth uses 3LO; Snowflake 2LO requires a compatible external OAuth
integration. Selecting 2LO in Studio does not add that capability to an upstream
provider.

For Runtime IAM, deploy an MCP Runtime with IAM inbound authentication and grant
the Gateway's service role `bedrock-agentcore:InvokeAgentRuntime` for that exact
Runtime and its DEFAULT endpoint. Paste the canonical invocation URL with the ARN
URL encoded and `?qualifier=DEFAULT`. The platform saves the native
`GATEWAY_IAM_ROLE` provider with signing service `bedrock-agentcore` and this
installation's region. Saving authentication does not grant IAM permissions.
It pins the exact Runtime URL, in addition to its HTTPS origin.

The [Snowflake setup guide](snowflake-setup.md) includes an
operator appendix for separately managed Python MCP stacks.

## Existing single-file deployments

The single-file bundle overlay is no longer offered for new onboarding. Existing
deployment records, runtime versions, authentication and registrations remain
available. Use their saved endpoint to register an existing server, or remove an
unused Runtime from **Manage uploaded MCP servers → Uploaded MCP deployments**. New servers use the complete
ZIP workflow above. Legacy API receipts keep their original configuration and
can be reconciled without changing their bytes or provider bindings.

## Edit and delete

1. Expand **Manage saved authentication**. For an editable API-key/PAT
   authentication row, choose **Edit authentication**.
   An empty replacement value keeps its current secret; stored values are never
   displayed. OAuth client details cannot be edited through this control.
2. Select an MCP registration and choose **Edit connection**, then **Save and
   rediscover**. Review the new tools and **Approve and publish**.
3. For an unused registration, choose **Delete connection** and type its exact name.
   Studio retires its Registry record, Gateway target, catalog entries and grants.
   The remote MCP server and saved credentials remain.
4. To remove a Studio-hosted server, expand **Manage uploaded MCP servers** and
   select it in **Uploaded MCP deployments**,
   choose **Delete MCP deployment**, type the exact deployment name and confirm.
   This also works for failed deployments and unfinished uploads. If a registration
   still points at it, remove that unused registration first. Saved-agent
   references block removal.
   Studio immediately fences new registrations, then deletes its exact Runtime,
   generated workload identity, ZIP version and upload-part versions. Wait for
   **MCP deployment deleted**. The deleted deployment no longer uses a slot.
   Shared roles, saved authentication and remote data remain; diagnostic logs
   retain their existing 14-day retention. Audit receipts remain as tombstones.
   After an uncertain response, choose **Check deletion status**. Reconciliation
   reads existing outcomes; a missing deletion requires **Retry deletion step**
   with the displayed job. Refreshing the page never repeats a delete request.
5. For unused Studio-created credentials, choose **Delete authentication** and
   confirm the exact name. The provider is removed and the secret enters a
   seven-day recovery period. For IAM connections, only saved connection metadata
   is deleted; the Runtime and its AWS permissions remain. IAM endpoints cannot
   be edited: replace an unused connection to select a different Runtime.
   OAuth reference deletion also preserves the provider and secret. Deleting an
   unused Studio-created OAuth provider removes it and schedules its secret for
   recovery, as with API-key credentials. OAuth
   registration editing requires reviewing its supplied tool schema.

Saved agent versions protect their MCP dependencies from changes and deletion.
MCP registrations protect the credentials they reference. Studio lists affected
consumers. Deployment-managed credentials are labeled and protected. For a new
OAuth client, create a new authentication connection and attach it to an
unreferenced registration through **Edit connection**, then review and publish.
Studio rejects duplicate registrations for the same endpoint. If saved agents
block editing, inspect and intentionally resolve those dependencies; preserving
them while testing a replacement may require a separate server endpoint.
Deleting Studio credentials does not revoke a token at the upstream provider.

## Recovery

The browser retains request IDs for onboarding/publication and authentication
setup, never credential values. On an uncertain response, use the displayed status
or reconciliation action before attempting another mutation.

- **Check authentication status** reads the recorded secret/provider state.
- **Check IAM connection status** reads the saved nonsecret IAM request. After a
  confirmed missing request, **Retry retained IAM request** explicitly resends the
  original name, URL and request key, even if the form has since changed.
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

Python hosting has a separate protected `mcp-python-config` setting, so installing
it does not alter existing Gateway registrations. Install it with the existing
[`facade_deploy.py`](../examples/runtime-snowflake-mcp/facade_deploy.py) workflow:
`plan`, `deploy`, then `configure`, using `--python-config <file>` and a retained
`--state-dir <directory>`. The configuration contains:

```json
{
  "prefix": "studio-python-mcp",
  "profile": "your-verified-profile",
  "account": "123456789012",
  "region": "us-east-1",
  "bundle_name": "Snowflake MCP / Python 3.13",
  "bundle_digest": "<sha256 of the approved ZIP>",
  "python_onboarding": {
    "table_name": "<existing Studio state table>",
    "runtime_prefix": "studio_python_mcp",
    "deployment_prefix": "<Studio credential prefix>",
    "worker_role_name": "<existing Studio worker role>",
    "artifact": {
      "bucket": "<private versioned release bucket>",
      "key": "mcp/python/bundles/<sha256>.zip",
      "version_id": "<immutable S3 version>"
    }
  }
}
```

First place the approved ZIP in that versioned key with encryption, its digest,
and `auto-delete=no`; record the upload intent and verify the stored bytes.
The bridge reserves ten Lambda executions per function by default. For an account
whose Lambda quota cannot support that reservation, add
`"reserved_concurrency": null` to this configuration to use its shared unreserved
capacity, or specify an available positive reservation per function. The generated
template uses the supplied artifact bucket and requires no CDK bootstrap stack.
Use the example's locked dependencies for a new installation, or copy the exact
version from the accepted Snowflake MCP Runtime. The installer checks the AWS
account, existing table and worker, bundle bytes, authenticated bridge, native
resource tags and terminal deployment states before `configure` enables uploads.
Its managed deployment policy grants only the selected Runtime/package namespace.

The app release moves worker onboarding permissions from its near-full inline
policy set into `McpOnboardingPolicy`. The release installer verifies the native
managed-policy retention tags before UI publication. Both the 6,144-byte managed
policy limit and the worker's 10,240-byte aggregate inline limit remain enforced.
Update the existing machine Gateway's `McpCredentialUse` policy from
`infra.mcp_onboarding.configure_gateway` when enabling 2LO.

For release rollback, retain the previous frontend index version, exact Lambda
packages and configuration hashes, CloudFormation templates and operation
receipts. Stop starting new uploads and let active jobs reconcile before restoring
the verified preceding frontend and Lambda packages. A CloudFormation artifact
parameter can predate a scoped Lambda hotfix: use the recorded live package for
each function. Retain uploaded Runtimes, credential secrets and source records;
do not delete resources or overwrite their saved identities as a rollback shortcut.
Access the Studio through its Cognito login and the generated MCP through its
authenticated CloudFront endpoint; Lambda Function URLs are never required.

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
