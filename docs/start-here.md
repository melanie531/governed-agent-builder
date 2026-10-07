# Configure MCP and create an agent in Agent Studio

This guide takes you from an MCP server to an agent that calls its tools through
Studio. Use your organization's Studio URL and your own server and identity
provider settings. Replace values written as `<PLACEHOLDER>` with your values.

Platform administrators connect servers and publish tools. Business users create
agents from the capabilities available in their workspace. Your account may have
both roles; switch views through the profile menu when needed.

**Setting up Snowflake for the first time?** Use the
[complete managed MCP and new OAuth provider walkthrough](snowflake-setup.md#b-managed-mcp-with-user-sign-in-3lo).
It starts with Snowflake object creation, shows the commands that supply every
**Create provider** field, and continues through callback registration,
publication and a business user's agent test. An existing provider is not needed.

## Before you start

| You need | Where to get it |
| --- | --- |
| Studio URL and sign-in access | Your platform administrator or invitation |
| Permission to manage MCP connections | A Platform Admin role in Studio |
| A workspace for publication and agent creation | Your organization's configured Studio workspaces |
| An MCP server | An existing HTTPS Streamable HTTP endpoint, or a complete deployment ZIP |
| The server's authentication requirements | The server owner or its documentation |
| An approved model and agent template | Your workspace's AI Catalog and Create agent page |

Provider access is separate from Studio access. Publishing a connection does not
grant users permissions to the external system's data.

## Choose where to continue

| What you already have | Next step |
| --- | --- |
| No Snowflake MCP or OAuth provider yet | [Snowflake managed MCP with a new OAuth integration and provider, B1–B10](snowflake-setup.md#b-managed-mcp-with-user-sign-in-3lo) |
| An existing hosted MCP endpoint | [1A. Connect a hosted server](#1a-connect-a-hosted-server) |
| A ZIP that has not been deployed | [1B. Upload a complete package](#1b-upload-a-complete-package) |
| An uploaded package marked **READY** | [1C. Reuse a ready package](#1c-reuse-a-ready-package) |
| Saved authentication for this exact endpoint | [2E. Reuse authentication](#2e-reuse-authentication) |
| A published MCP connection | [4. Create an agent](#4-create-an-agent) |
| A deployed agent with the intended MCP selected | [5. Test the agent](#5-test-the-agent) |
| Any Snowflake setup: managed MCP with PAT or 3LO, or an uploaded Python MCP | [Snowflake setup: choose your path and follow it through agent testing](snowflake-setup.md) |
| A connection whose credentials need replacing | Read [Change an existing setup](#change-an-existing-setup) before creating or deleting anything |

## The portal journey

Open **MCP servers → Add MCP connection** once. The wizard keeps server,
authentication, tools and publication together:
**Server → Authentication → Tools and workspaces → Review and publish**.
You do not need to create authentication in a separate dialog.

| Step | Who | What to do | Expected result |
| --- | --- | --- | --- |
| 1 | Platform administrator | Connect a hosted endpoint or select a deployed package | An MCP endpoint and connection name |
| 2 | Platform administrator | Save the appropriate authentication connection | **Authentication saved** |
| 3 | Platform administrator | In **Tools and workspaces**, load tool definitions when requested and choose workspaces; in **Review and publish**, select tools and publish | **Connection published**, status **Ready** |
| 4 | Business user | Select a template, model, MCP and tool permissions; deploy | Agent status **Deployed** |
| 5 | Business user | Send a specific tool request; authorize provider access when required | A real tool result visible in Chat and Execution details |

An **uploaded deployment** is the running server. An **authentication connection**
stores how Studio accesses it. An **MCP connection** publishes that endpoint and
its selected tools. An **agent** uses the published tools according to its
instructions. These are separate objects, so uploading a ZIP is only the first
part of setup.

## 1. Select the MCP server

As Platform Admin, open **Platform governance → MCP servers → Add MCP
connection**. In **Server**, give the connection a descriptive, unique name. The description is
optional.

### 1A. Connect a hosted server

1. Choose **Connect hosted endpoint** under **MCP source**.
2. Enter the server's full HTTPS Streamable HTTP URL in **MCP endpoint URL**.
   Obtain this URL from the server's deployment details or its owner. A
   provider's website, authorization endpoint or token endpoint is a different
   address.
3. Choose **Next** to open [Authentication](#2-save-authentication).

This path registers an already running server. Studio does not deploy that
server. A local stdio command is not an HTTPS endpoint; its owner must expose a
supported HTTP service or supply a deployable package.

### 1B. Upload a complete package

1. Choose **Upload MCP package (.zip)**.
2. Select the deployment ZIP in **MCP package ZIP**.
3. Click **Upload and deploy package**.
4. Use **Check package status** until the package reaches **READY**.
5. Click **Use this MCP package**, then **Next** to open **Authentication**.
   Studio carries the endpoint and discovered tools into the following steps.

The package must contain the complete server:

| Requirement | What belongs in the ZIP |
| --- | --- |
| Entry point | `main.py` at the ZIP root, with no enclosing project directory |
| Supporting files | All imported application modules and required resource files |
| Third-party dependencies | Installed dependencies built for Python 3.13 on Linux ARM64 |
| HTTP service | MCP Streamable HTTP listening on `0.0.0.0:8000/mcp` |
| Size | At most 64 MiB compressed and 256 MiB expanded |
| Configuration | The server's non-secret startup settings; credentials are configured separately |

Studio deploys the ZIP's contents. It does not install a `requirements.txt` file
or supply missing provider code. Ask the package author for a deployment ZIP,
or build the [complete package example](../examples/complete-mcp-package/README.md).

Packages that use OAuth or API keys must implement the supported bearer-token
validation contract. Otherwise Studio offers IAM access for the package. Package
authors can follow the
[bearer-token package requirements](generic-mcp-onboarding.md#packages-that-accept-provider-bearer-tokens).

If transfer is interrupted, use **Check package status**, select the same ZIP
when requested, and choose **Resume same package**. Continue the retained upload.

### 1C. Reuse a ready package

1. Open **Add MCP connection** and choose **Use a deployed package** in **Server**.
2. Select the deployment under **Saved MCP package deployments**.
3. Click **Use this MCP package**.
4. Choose **Next** to open **Authentication**. Selecting the saved package does not upload or
   deploy another server.

If the endpoint already has a published registration, select that registration under
**Registered MCP connections** instead of submitting a duplicate.

### Find the MCP endpoint URL

After selecting **Use this MCP package**, Studio fills **MCP endpoint URL** in
**Server**. In **Authentication**, **Server endpoint** shows the address for
your chosen method. IAM and bearer authentication can use different addresses
for the same deployment; Studio selects the appropriate one.

For an already registered server, read **Endpoint** under **Registered MCP
connections**. For an uploaded server, expand **Manage uploaded MCP servers**,
select its row, and use **Copy MCP endpoint URL** or **Copy AWS IAM endpoint URL**
when shown.

## 2. Save authentication

In the wizard's **Authentication** step, choose the method required by the
server. Follow one setup path, then choose **Next** to open **Tools and workspaces**.

| Authentication method | Use it when | User sign-in to the provider |
| --- | --- | --- |
| **API key / PAT** | The server accepts a shared API key or personal access token | No OAuth consent step |
| **User sign-in (OAuth 3LO)** | Tools must use each user's provider identity and permissions | Required when valid authorization is unavailable |
| **Service credentials (OAuth 2LO)** | The provider supports the `client_credentials` grant for service access | No end-user consent |
| **AWS IAM / AgentCore Runtime** | Gateway must invoke an IAM-protected AgentCore Runtime | No provider OAuth consent |
| **Existing connection** | Suitable authentication is already saved for this endpoint | Depends on that connection's method |

Studio derives the authentication name from the connection name, for example
by adding ` OAuth` or ` IAM`, and attaches it when saved. The optional
**Manage saved authentication → Add authentication connection** control on the
overview is for administrators managing credentials separately. It is not
required for this journey.

The endpoint in this dialog is the MCP URL from step 1. The OAuth issuer and
authorization/token URLs identify the provider and have their own fields.

### 2A. API key or PAT

Obtain a token authorized to call this server from its provider or owner.

| Field | What to enter |
| --- | --- |
| **API key or PAT** | The token value |
| **Header name** | The header required by the server, such as `Authorization` or `X-API-Key` |
| **Header prefix** | `Bearer` for a bearer token; empty if the server expects the raw key |

Click **Save authentication**. Expected result: **Authentication saved**.
The token's external permissions determine what this connection can access.
Keep it out of the ZIP, endpoint URL and agent instructions.

### 2B. User sign-in (OAuth 3LO)

This path uses an authorization-code client. Each user grants access to their
provider account.

**Using Snowflake?** Follow the [first-time walkthrough](snowflake-setup.md#b-managed-mcp-with-user-sign-in-3lo).
If this form is already open, keep it open and start at
[B3: create the Snowflake integration](snowflake-setup.md#create-a-new-snowflake-oauth-integration).
B4 retrieves the credentials and URLs; B5 maps them to every field below; B6
registers the callback. The same file contains the separate PAT and Python
package paths.

1. In the provider's administration portal, create or obtain an OAuth application
   that supports authorization-code access to the MCP server's API.
2. Obtain its client ID, client secret, OAuth metadata and required scopes.
3. In Studio, choose **User sign-in (OAuth 3LO)**, then **OAuth provider setup →
   Create provider**.
4. Fill the following fields.

| Field | Where to find the value |
| --- | --- |
| **OAuth client ID** | The provider's OAuth application registration |
| **OAuth issuer** | The provider's documented issuer or discovery metadata `issuer` |
| **Authorization endpoint** | Provider documentation or metadata `authorization_endpoint` |
| **Token endpoint** | Provider documentation or metadata `token_endpoint` |
| **Client authentication** | The method the provider supports: **HTTP Basic** for `client_secret_basic`, or **Client secret in POST body** for `client_secret_post` |
| **OAuth client secret** | The secret issued for this OAuth application |
| **OAuth scopes** | Scopes required by the server's API, separated by spaces |

There is no universal scope value. Ask the server owner which scopes its tools
require, then enable those permissions on the provider application. The client
secret authenticates the application; it is different from a user's password.

**Create provider** registers these details in AWS for Studio. It does not create
the upstream provider's OAuth application. For Snowflake, the integration is
created by the SQL in the applicable setup guide linked above.

5. Click **Save OAuth connection**. Wait for **Authentication saved**. Studio
   selects the saved authentication for this connection.
6. Copy the generated callback URL and register it on the provider application
   if it is not already allowed, as described below.

#### Check the OAuth callback

**The callback is generated by AgentCore. There is no callback URL input in
Studio.**

| Where you are | Where to copy the callback |
| --- | --- |
| Still in **Authentication** after saving OAuth | **Check the OAuth callback** panel |
| Authentication was saved separately, or you have left the wizard | **MCP servers → Manage saved authentication → Refresh credentials → select your OAuth row → OAuth callback URL** below the table |

Check the application's allowed callback/redirect URIs in the provider's portal.
If this exact URL is already allowed, continue without changing the application.
Otherwise add it, preserving other callbacks. Keep the scheme, host, path and
generated identifier unchanged. The MCP endpoint and Studio home page are
different URLs.

Some providers require a redirect URI before issuing client credentials.
Follow their supported initial registration process and complete the callback
configuration before allowing sign-in. The
[Snowflake example](snowflake-setup.md#create-a-new-snowflake-oauth-integration)
uses `<TEMPORARY_HTTPS_URL>` only while the new integration is disabled.
Replace that temporary value with the full AgentCore callback copied from Studio
before enabling the Snowflake integration or signing in.

Saving authentication and registering a callback prepare the connection.
User consent and real data access are checked when the agent calls a tool.

#### Use an existing AWS OAuth provider

Choose **Existing provider** only if your platform administrator supplies a
compatible **OAuth provider ARN** already registered for this Studio
installation. Enter that ARN and the required scopes, then save. Recheck the
returned callback against the upstream application's allowed redirects.

The ARN identifies an AWS credential provider. It is not the upstream client
ID, provider application name, or an MCP URL. If you are creating a provider
through Studio, you do not need to find or enter an ARN.

### 2C. Service credentials (OAuth 2LO)

Follow this separate path when the provider supports service access through
the `client_credentials` grant and the MCP server accepts the resulting token.

1. Create or obtain a service OAuth application in the provider's administration
   portal. Have its administrator assign the necessary application permissions.
2. Choose **Service credentials (OAuth 2LO)** in Studio.
3. Fill the following fields.

| Field | Where to find the value |
| --- | --- |
| **OAuth client ID** | The service application's registration |
| **OAuth discovery URL** | The provider's HTTPS OAuth/OpenID discovery document |
| **Client authentication** | A supported method from the provider documentation or discovery metadata |
| **OAuth client secret** | The secret issued for the service application |
| **OAuth scopes** | The provider's service-access scopes, separated by spaces |

4. Click **Save OAuth connection** and wait for **Authentication saved**.
5. Choose **Next** to open **Tools and workspaces**.

This flow has no end-user callback or consent step. Choosing 2LO in Studio
does not enable that grant at the provider. Use 3LO when access must follow
each user's identity and permissions.

### 2D. AWS IAM / AgentCore Runtime

1. Choose **AWS IAM / AgentCore Runtime**.
2. For a Studio-uploaded package, keep the generated Runtime endpoint. For a
   separately hosted Runtime, obtain its canonical invocation URL from the
   server owner. It contains the URL-encoded Runtime ARN and ends in
   `/invocations?qualifier=DEFAULT`.
3. Click **Save IAM connection** and wait for **Authentication saved**.

The Runtime must support IAM invocation in this Studio installation's AWS
account and region. Its operator must grant the Gateway role permission to
invoke that Runtime. Saving authentication records the settings; it does not
grant AWS permissions. This flow requires no API key, OAuth client secret or
OAuth scopes.

### 2E. Reuse authentication

In **Authentication**, choose **Authentication method → Existing connection**,
then select the saved **Authentication connection** intended for this endpoint.
Choose **Next**. This also attaches authentication created through the optional
management dialog; it does not create another credential.

Per-user OAuth and IAM connections are bound to their exact endpoint. A shared
hostname alone does not make a saved connection suitable for a different server.
Use the current endpoint and the corresponding saved authentication.

If OAuth setup is pending, use **Check OAuth connection status** and
**Continue OAuth setup** when offered. For other methods, use their displayed
status controls. Resolve the retained request before creating another one;
secrets may need to be reentered for an explicit retry.

## 3. Review tools and publish

Stay in the wizard. After saving or selecting authentication, choose **Next**.

1. In **Tools and workspaces**, 3LO needs tool definitions before publication.
   Uploaded packages show **Tools loaded from your package** automatically.
   For a Snowflake SQL server, query `DESCRIBE MCP SERVER` and paste its complete
   `server_spec` value using **Paste tool definitions instead**. Studio prepares
   the SQL inputs automatically; follow the
   [Snowflake import steps](snowflake-setup.md#b7-load-the-tool-definition-and-choose-workspaces).
   For other hosted servers, choose **Choose tool definitions file** or paste
   the server owner's MCP definitions. Standard `{"tools":[...]}` and JSON-RPC
   `result.tools` formats remain supported, up to 180 KB and 100 tools. Review
   the displayed tool names. Missing or invalid definitions disable **Connect and review**.
   Other authentication methods discover the tools automatically.
2. Select **Visible workspaces**. These are the Studio workspaces where users
   should be able to find this MCP; they are separate from OAuth scopes and
   provider data permissions.
3. Click **Connect and review** for 3LO, or **Connect and discover** for the
   other authentication methods.
4. In **Review and publish**, inspect the tool names, descriptions and parameters. Select
   the tools you want to publish.
5. Click **Approve and publish** and wait for **Connection published** and
   status **Ready**.

For 3LO, the supplied schema makes the tools available for review without
using an administrator's provider session. Publication is not a successful
user-consent or data-query test; complete step 5.

The catalog stores the MCP connection and its tools as separate capabilities.
The administrator's **Registry & AI Catalog** can show one server row with
its tools under **Tools exposed by this MCP connection**.

## 4. Create an agent

Switch to the Business User view and the workspace where the MCP was published.

1. Open **AI Catalog** and confirm the intended model and MCP are available.
   Use **Request access** where required. An authorized administrator handles
   requests in **Policies & approvals**; the requester cannot approve their
   own request by switching roles.
2. Open **Create agent** and select an available template appropriate to the
   task. For a simple tool test, **Knowledge Q&A** can be used if your
   installation offers it. Click **Next**.
3. Enter an agent name and select an approved **Model** that supports tool use.
4. Under **MCP servers**, select the connection published in step 3.
5. Expand its **Tool permissions** and allow the tools this agent needs.
   Select relevant **Skills** if available.
6. Write instructions describing the task, when to use tools, required inputs
   and how to handle errors. For a first test, adapt:

   ```text
   Use the selected MCP's tools to answer the user's request.
   Ask for missing required inputs before calling a tool.
   Use only the tools needed for the request.
   Report the returned result accurately. If a tool fails, explain the error.
   Request provider authorization when required.
   ```

7. Click **Next**. Leave **Evaluation dataset JSON** empty for the initial
   manual test, or supply a dataset if you also want an evaluation.
8. Continue to review. Check the model, selected MCP, tool permissions, skills
   and instructions, then click **Deploy to AgentCore**.
9. Wait for **Agent overview → Deployment → Deployed**. Open **Chat**.

For an existing agent, choose **Revise agent**, make the intended selection
changes and deploy the new version. Publishing more tools on an MCP does not
automatically add them to an already saved agent version.

## 5. Test the agent

Start with one read-only tool and explicit inputs from its schema. This checks
the connection before asking the model to plan a larger task.

### Make a controlled tool call

Open **Chat**, enter a request in **Your question**, then click **Send message**.
Replace both placeholders before using this request:

```text
Call <TOOL_NAME> exactly once with these arguments: <ARGUMENTS>.
Return the actual tool result. If it fails, report the error.
```

If you used the complete package example linked in step 1B, send:

```text
Call package_greeting exactly once with name="Example".
Return the greeting and package_marker from the tool result.
```

That example should return `Hello, Example!` and `complete-package-v1`.
For another server, define the expected result using its documentation and
your own test data.

### Complete provider sign-in when required

For 3LO, Studio opens a provider tab when authorization is needed. Use the
browser profile you normally use for that provider, especially if it holds
your passkey. Sign in, review the requested access and approve it.

If the popup is blocked, use **Open provider sign-in** when shown. Return to
Studio afterward. If the page says the dialogue will continue, wait for that
run. If it explicitly requires resubmission, review the restored question and
send it once.

An existing provider session or valid authorization may avoid another login
prompt. API-key, IAM and 2LO connections do not require per-user OAuth consent.

#### Recover an expired sign-in

Studio's provider authorization request lasts ten minutes. Complete sign-in
in the same browser profile that opened it. If you leave the provider page open
and return later, check Studio before continuing on that old page.

If Studio shows **Sign-in expired**, return to the existing agent's **Chat**
page. Review any completed tool calls, then enter the intended test question in
**Your question** and click **Send message** once to request a new sign-in link.
Typing the question enables the button. Use the newly opened tab or the new
**Open provider sign-in** link and complete it promptly. Keep the existing MCP,
authentication connection and agent.

A callback error saying `Value at 'cookie' ... Member must not be null` means
the callback did not receive a required browser cookie. An expired sign-in
page is one possible cause. Do not reload or reuse that callback URL. Return to
Studio and follow the expired-sign-in steps above when the saved request has
expired. If the same error occurs with a fresh request in the same browser
profile, record the error and contact your platform administrator.

### Verify the result

| Check | What you should see |
| --- | --- |
| Tool execution | **Execution details → Gateway tools called** includes the intended tool |
| Error status | No `(error)` marker for the tool and no **Run failed** result |
| Response | The actual result matches the expected value or result shape for your inputs |
| Provider access | A real authorized read succeeds, if the first tool only tested connectivity |
| Persistence | After reloading the agent page, the saved conversation is still visible |

A model's statement that a connection works is insufficient without the actual
tool execution and result. Retain the displayed trace/request ID when a run fails.

After the controlled test passes, ask the agent to perform your real task.
A broader request can legitimately require several tool calls. Supply relevant
object names, identifiers or filters when you want a narrower operation.

Use **New conversation** for an independent test so previous chat context does
not affect the result.

## Change an existing setup

| Change | Supported path |
| --- | --- |
| Update an unused API key/PAT connection | Select its authentication row → **Edit authentication**. Leave the replacement value empty to retain it. |
| Use a different OAuth client | Create a new authentication connection. The current **Edit authentication** control does not edit OAuth client details. |
| Update an unused MCP registration | Select it → **Edit connection** → choose its authentication and other settings → **Save and review** for 3LO, or **Save and rediscover** otherwise → review and publish again. |
| Change the tools available to an agent | **Revise agent**, update the selections and deploy the new version. |

Saving an MCP edit withdraws the previous publication until review and
publication finish. Saved agent versions can block editing or deleting their
MCP dependencies, including references from older versions.

Studio also rejects a second registration for the same endpoint. Changing the
connection name does not bypass that restriction. If references block an edit,
inspect the listed consumers. Retire only resources you intend to remove.
Preserving those agents while testing a replacement may require a separate
server endpoint; the current portal does not provide in-place OAuth-client
migration for a referenced connection.

## Remove resources you no longer need

Review each object's dependencies before confirming deletion.

| Object | Portal action | Effect |
| --- | --- | --- |
| Agent | **Delete agent** | Removes the selected agent and its managed resources |
| MCP registration | **Registered MCP connections → Delete connection** | Removes publication and Gateway/Registry bindings; retains the server and authentication |
| Uploaded server | **Manage uploaded MCP servers → select the deployment → Delete MCP deployment** | Deletes that hosted Runtime and uploaded package |
| Saved authentication | **Manage saved authentication → select the row → Delete authentication** | Removes the unused saved connection; the confirmation explains whether its AWS provider and stored secret are also removed |

Delete dependent test agents before their registration, and remove registrations
before deleting a hosted deployment or its authentication. Enter the exact name
when requested and wait for the deletion success message. Removing Studio
authentication does not delete the upstream provider's OAuth application or data.

## Troubleshoot the failed step

| Symptom | Next check |
| --- | --- |
| Upload option or an authentication method is unavailable | Confirm Platform Admin access, hosting enablement and package compatibility with your administrator. |
| ZIP rejected | Check root `main.py`, bundled dependencies, Python/platform compatibility, archive paths and size limits. |
| Package is ready but its endpoint is not visible | Select the saved package, click **Use this MCP package**, choose authentication and read **MCP endpoint URL** in the form. |
| OAuth callback field is missing | Expected. Save OAuth first, select its authentication row and copy **OAuth callback URL**. |
| Redirect URI mismatch | Compare the generated callback and the upstream application's allowed redirects; verify Studio uses that application's client ID. |
| OAuth setup did not complete | Check the saved request status and use the offered continuation/retry control. Avoid starting duplicate requests. |
| Provider sign-in expired or the callback reports a missing cookie | Follow [Recover an expired sign-in](#recover-an-expired-sign-in). Start from the existing agent in Studio; do not reuse the old provider or callback page. |
| Invalid scope or insufficient provider permissions | Check the provider's documented scopes, application permissions and the authorizing user's data access. |
| MCP does not appear in the agent builder | Check publication status, visible workspaces and the user's catalog access. |
| Existing connection cannot be edited | Inspect saved-agent references or unfinished operations; follow **Change an existing setup** above. |
| Agent fails after provider sign-in | Record the error, trace/request ID and tool name. A runtime error alone does not identify OAuth as the cause. |

For package authors and platform operators, see the
[MCP implementation and administration reference](generic-mcp-onboarding.md).
