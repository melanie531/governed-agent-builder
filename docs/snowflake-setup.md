# Snowflake setup: MCP connection, authentication and agent testing

Use this guide to prepare Snowflake, connect its tools in Agent Studio and test
an agent through Chat. All object names are examples; replace `<PLACEHOLDERS>`
with your own values.

**For a first setup with a Snowflake-managed MCP and individual user sign-in,
follow B1–B10 below in order.** This walkthrough creates a new Snowflake OAuth
integration and a new AWS OAuth provider. You do not need an existing provider
or an OAuth provider ARN.

Already at Studio's **Authentication → Create provider** form? Keep that wizard
open. Complete [B3](#create-a-new-snowflake-oauth-integration) and
[B4](#retrieve-the-snowflake-client-details), then enter the values from the
[B5 field table](#create-the-aws-oauth-provider-in-studio) in your current form.
Continue with B6; there is no need to start another MCP connection.

## Choose your setup

| Setup | Where the MCP server runs | Whose Snowflake access is used | Steps |
| --- | --- | --- | --- |
| **B. Managed MCP with user sign-in (3LO)** | Snowflake | Each business user's Snowflake identity and reader role | [Complete first-time walkthrough, B1–B10](#b-managed-mcp-with-user-sign-in-3lo) |
| **A. Managed MCP with PAT** | Snowflake | One service user shared by the agents using this connection | [PAT walkthrough](#a-managed-mcp-with-pat) |
| **C. Python MCP with user sign-in (3LO)** | A complete Python ZIP uploaded to Studio | Each business user's Snowflake identity and reader role | [Build, upload and configure the Python server](#c-uploaded-python-mcp-with-user-sign-in-3lo) |

For partially completed setups, use [Continue an existing setup](#continue-an-existing-setup).

### What the names and URLs mean

| Item | What it is | Where to get it |
| --- | --- | --- |
| Snowflake-managed MCP server | A Snowflake object exposing tools at an HTTPS endpoint | `CREATE MCP SERVER` in B2 |
| Snowflake OAuth integration | The OAuth client: client ID, secret, allowed roles and callbacks | `CREATE SECURITY INTEGRATION` in B3; retrieve values in B4 |
| AWS OAuth provider | AgentCore's configuration for that Snowflake OAuth client | Studio creates it when you choose **Create provider → Save OAuth connection** in B5 |
| Saved authentication connection | A named Studio record binding credentials to an endpoint | The wizard's **Authentication** step; optional administration under **Manage saved authentication** |
| Registered MCP connection | The endpoint and tools published for selected workspaces | **Registered MCP connections** after onboarding |
| Connection / authentication name | A label you choose, such as `Snowflake reader` | It need not match a Snowflake object name |
| MCP endpoint URL | The full URL that accepts MCP requests | [Managed endpoint](#get-a-snowflake-managed-mcp-endpoint) or the uploaded package's **MCP endpoint URL** |
| OAuth issuer, authorization and token endpoints | Snowflake's OAuth URLs | B4 derives the issuer from the authorization endpoint returned by `DESCRIBE SECURITY INTEGRATION` |
| OAuth callback URL | Where Snowflake returns the user after consent | Studio displays it **after** saving the new provider; register it in Snowflake in B6 |

The MCP server supplies the tool endpoint. The security integration supplies
the OAuth credentials. Creating one does not create the other.

## B. Managed MCP with user sign-in (3LO)

Complete this sequence for a new setup:

| Step | Where | Result |
| --- | --- | --- |
| B1 | Snowflake | Sample data and a reader role granted to the person who will sign in |
| B2 | Snowflake | An MCP server and its full endpoint URL |
| B3 | Snowflake | A new, initially disabled OAuth integration |
| B4 | Snowflake | Client ID, secret, OAuth URLs and role scope for the Studio form |
| B5 | Studio: Server, then Authentication | A new OAuth provider and **Authentication saved** |
| B6 | Studio, then Snowflake | The generated callback registered and the integration enabled |
| B7 | Studio: Tools and workspaces | `query_sql` loaded and the intended workspace selected |
| B8 | Studio: Review and publish | **Connection published**, status **Ready** |
| B9 | Studio: Business User | A deployed agent using this connection |
| B10 | Studio: Chat | Snowflake consent and verified identity/data results |

In Studio, B5–B8 stay in one **Add MCP connection** wizard:
**Server → Authentication → Tools and workspaces → Review and publish**.
During B6, keep it open while updating the new integration in a Snowflake tab.

<a id="prepare-snowflake-access"></a>

### B1. Prepare Snowflake access

**Who:** a Snowflake administrator prepares objects and grants; a Studio platform
administrator publishes the connection; a business user creates and tests the
agent. One person may hold these roles. Each person testing 3LO needs their own
Snowflake sign-in and the reader-role grant. Studio must have an approved
tool-capable model available in the selected workspace.

In Snowsight, open **Projects → Workspaces**, create a SQL file and select an
administrator role allowed to create the objects and grants below. Installations
with older navigation can use **Worksheets → SQL Worksheet**. Creating the OAuth
integration in B3 requires `ACCOUNTADMIN` or a role with `CREATE INTEGRATION`.
Use the integration owner to retrieve its secret and change its settings.

First, run:

```sql
SELECT CURRENT_ORGANIZATION_NAME() AS ORGANIZATION_NAME,
       CURRENT_ACCOUNT_NAME() AS ACCOUNT_NAME,
       CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME()
         AS MCP_ACCOUNT_IDENTIFIER,
       CURRENT_USER() AS SNOWFLAKE_USER;
```

Record the results. Open Snowsight's account menu, find the account details and
copy **Account URL**, for example `https://<organization>-<account>.snowflakecomputing.com`.
Use that Snowflake account address for B2. The browser's `app.snowflake.com`
address is the Snowsight UI and is not the MCP host.

The commands use these example names consistently:

| Value | Example used below |
| --- | --- |
| Warehouse | `MCP_READ_WH` |
| Database and schema | `MCP_DEMO.DATA` |
| Sample table | `MCP_DEMO.DATA.MCP_SETUP_CHECK` |
| Reader role | `MCP_READER` |
| MCP server | `MCP_DEMO.DATA.READ_ONLY_MCP` |
| New OAuth integration | `STUDIO_MCP_OAUTH` |
| `<SNOWFLAKE_USER>` | The exact username of the person who will approve Snowflake access in B10 |

For a first test, choose unused names and create the small dataset below.
Replace example names consistently if they are already in use. `IF NOT EXISTS`
does not verify the contents of an existing object. If your administrator has
supplied approved existing data, skip sample-object creation and grant the reader
role access to that data instead.

```sql
CREATE WAREHOUSE IF NOT EXISTS MCP_READ_WH
  WAREHOUSE_SIZE = XSMALL AUTO_SUSPEND = 60 AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE;
CREATE DATABASE IF NOT EXISTS MCP_DEMO;
CREATE SCHEMA IF NOT EXISTS MCP_DEMO.DATA;
USE WAREHOUSE MCP_READ_WH;

CREATE TABLE IF NOT EXISTS MCP_DEMO.DATA.MCP_SETUP_CHECK (
  ID INTEGER, LABEL VARCHAR
);
MERGE INTO MCP_DEMO.DATA.MCP_SETUP_CHECK AS target
USING (
  SELECT column1 AS ID, column2 AS LABEL
  FROM VALUES (1, 'first sample'), (2, 'second sample')
) AS source ON target.ID = source.ID
WHEN NOT MATCHED THEN INSERT (ID, LABEL) VALUES (source.ID, source.LABEL);
```

Expected result for a fresh sample table: two rows.

Create the role and grant access to the sample:

```sql
CREATE ROLE IF NOT EXISTS MCP_READER;
GRANT USAGE ON WAREHOUSE MCP_READ_WH TO ROLE MCP_READER;
GRANT USAGE ON DATABASE MCP_DEMO TO ROLE MCP_READER;
GRANT USAGE ON SCHEMA MCP_DEMO.DATA TO ROLE MCP_READER;
GRANT SELECT ON TABLE MCP_DEMO.DATA.MCP_SETUP_CHECK TO ROLE MCP_READER;
```

Grant this role to each person who will authorize access. Replace
`<SNOWFLAKE_USER>` before running:

```sql
GRANT ROLE MCP_READER TO USER <SNOWFLAKE_USER>;
SHOW GRANTS TO USER <SNOWFLAKE_USER>;
SHOW GRANTS TO ROLE MCP_READER;
```

The initial `CURRENT_USER()` result identifies the worksheet user. Use a
different username here if someone else will test the agent. Double-quote names
with special characters, for example `"person@example.com"`. A Studio login
does not create a Snowflake user or grant this role.

Warehouse access provides compute; database, schema and object grants determine
which data can be read. The agent's queries will use `MCP_READER`.

<a id="get-a-snowflake-managed-mcp-endpoint"></a>

### B2. Create the MCP server and get its endpoint

Run this in the same Snowflake SQL file. It creates a server with one tool,
`query_sql`, and lets the reader role use it:

```sql
CREATE MCP SERVER IF NOT EXISTS MCP_DEMO.DATA.READ_ONLY_MCP
FROM SPECIFICATION $$
tools:
  - name: "query_sql"
    title: "Query permitted Snowflake data"
    type: "SYSTEM_EXECUTE_SQL"
    description: "Run read-only SQL against objects allowed by the connected Snowflake role. Use fully qualified names and return only the data needed for the question."
    config:
      read_only: true
      warehouse: "MCP_READ_WH"
$$;

GRANT USAGE ON MCP SERVER MCP_DEMO.DATA.READ_ONLY_MCP TO ROLE MCP_READER;
DESCRIBE MCP SERVER MCP_DEMO.DATA.READ_ONLY_MCP;
```

In the `DESCRIBE` result, inspect `server_spec`: it should contain `query_sql`,
`SYSTEM_EXECUTE_SQL`, `read_only: true` and `MCP_READ_WH`. Snowflake returns this
server configuration as JSON. Keep the complete `server_spec` value for B7:
Studio accepts it directly and prepares the supported SQL tool inputs. You do
not need to write an `inputSchema` or obtain another access token.

For native MCP sessions, check that each connecting user has a default warehouse:

```sql
DESCRIBE USER <SNOWFLAKE_USER>;
```

Inspect `DEFAULT_WAREHOUSE`. If it is empty, have the administrator set an
approved value:

```sql
ALTER USER <SNOWFLAKE_USER> SET DEFAULT_WAREHOUSE = 'MCP_READ_WH';
```

`MCP_READ_WH` has the reader grant from B1. If you keep a different existing
default warehouse, have the administrator confirm that `MCP_READER` can use it.

To obtain the endpoint for the sample server, run:

```sql
SELECT
  'https://' ||
  LOWER(REPLACE(CURRENT_ORGANIZATION_NAME(), '_', '-') || '-' ||
        REPLACE(CURRENT_ACCOUNT_NAME(), '_', '-')) ||
  '.snowflakecomputing.com/api/v2/databases/MCP_DEMO/schemas/DATA/mcp-servers/READ_ONLY_MCP'
  AS MCP_ENDPOINT_URL;
```

Copy the **entire `MCP_ENDPOINT_URL` cell** for Studio. Check its host against
the account URL recorded in B1. Snowflake account hostnames use hyphens in place
of underscores. If your administrator supplied a different approved account
host, use that host with this path.

For other server names, the URL format is:

```text
https://<ACCOUNT_HOST>/api/v2/databases/<DATABASE>/schemas/<SCHEMA>/mcp-servers/<MCP_SERVER>
```

`<ACCOUNT_HOST>` includes `.snowflakecomputing.com`. Preserve object-name case
and URL-encode special characters in quoted object names. A URL in this format
works only after that server exists and the reader has its grants.

**Checkpoint:** you have the full MCP URL, the `query_sql` server specification
and a reader role with access to the server, warehouse and intended data.

<a id="configure-user-sign-in"></a>
<a id="create-a-new-snowflake-oauth-integration"></a>

### B3. Create a new Snowflake OAuth integration

Create a new integration for this walkthrough. This creates the OAuth client
whose details you will enter under **Create provider** in Studio.

Snowflake requires a redirect URI when creating the integration. AgentCore
generates the final callback only after B5. To complete setup in that order:

1. Copy your Studio site's HTTPS origin, such as `https://your-studio.example.com`,
   without the `/#mcp-servers` part.
2. Use it for `<TEMPORARY_HTTPS_URL>` below. This is a temporary value; no login
   should redirect there.
3. Keep `ENABLED = FALSE`. B6 replaces the whole value with the generated
   callback and then enables the integration.

Run as the Snowflake integration administrator:

```sql
CREATE SECURITY INTEGRATION STUDIO_MCP_OAUTH
  TYPE = OAUTH
  OAUTH_CLIENT = CUSTOM
  ENABLED = FALSE
  OAUTH_CLIENT_TYPE = 'CONFIDENTIAL'
  OAUTH_REDIRECT_URI = '<TEMPORARY_HTTPS_URL>'
  OAUTH_ISSUE_REFRESH_TOKENS = TRUE
  OAUTH_USE_SECONDARY_ROLES = NONE
  ALLOWED_ROLES_LIST = ('MCP_READER');
```

Use a new integration name if `STUDIO_MCP_OAUTH` already exists; do not use
`CREATE OR REPLACE` on an integration used by another connection. Substitute
your chosen name in every command below.

**Expected result:** the new integration exists with `ENABLED = FALSE`,
client type `CONFIDENTIAL`, refresh tokens enabled and `MCP_READER` allowed.
These settings configure authorization-code user sign-in (3LO).

<a id="retrieve-the-snowflake-client-details"></a>

### B4. Retrieve the values for the Studio form

Replace `STUDIO_MCP_OAUTH` below if using another integration, and `MCP_READER` if
using another role. In the same Snowsight SQL file, run `USE WAREHOUSE`, then
`DESCRIBE`, then the following `SELECT` in order. Do not run another statement
between `DESCRIBE` and `SELECT`: `LAST_QUERY_ID()` must refer to the description.

```sql
USE WAREHOUSE MCP_READ_WH;
DESCRIBE SECURITY INTEGRATION STUDIO_MCP_OAUTH;
SELECT
  MAX(CASE WHEN "property" = 'OAUTH_CLIENT_ID'
    THEN "property_value" END) AS OAUTH_CLIENT_ID,
  REGEXP_SUBSTR(MAX(CASE WHEN "property" = 'OAUTH_AUTHORIZATION_ENDPOINT'
    THEN "property_value" END), '^https://[^/]+') AS OAUTH_ISSUER,
  MAX(CASE WHEN "property" = 'OAUTH_AUTHORIZATION_ENDPOINT'
    THEN "property_value" END) AS AUTHORIZATION_ENDPOINT,
  MAX(CASE WHEN "property" = 'OAUTH_TOKEN_ENDPOINT'
    THEN "property_value" END) AS TOKEN_ENDPOINT,
  'session:role:MCP_READER refresh_token' AS OAUTH_SCOPES
FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()));
```

The `SELECT` returns one row with five columns. Keep that result available for
B5. It contains no client secret.

| Result column | What it supplies |
| --- | --- |
| `OAUTH_CLIENT_ID` | The client ID generated for the new integration |
| `OAUTH_ISSUER` | The HTTPS origin extracted from Snowflake's authorization endpoint |
| `AUTHORIZATION_ENDPOINT` | The URL Snowflake reports under `OAUTH_AUTHORIZATION_ENDPOINT` |
| `TOKEN_ENDPOINT` | The URL Snowflake reports under `OAUTH_TOKEN_ENDPOINT` |
| `OAUTH_SCOPES` | The role scope constructed for this example, plus `refresh_token` |

Snowflake may return an account-locator hostname instead of the account alias
used in your MCP URL. Copy the returned OAuth URLs. Their host does not have to
look identical to the MCP host.

If the query fails or returns empty values, rerun `DESCRIBE` followed immediately
by `SELECT`. You can also read `OAUTH_CLIENT_ID`, `OAUTH_AUTHORIZATION_ENDPOINT`
and `OAUTH_TOKEN_ENDPOINT` directly from the description. The issuer is the
authorization URL with `/oauth/authorize` removed.

Retrieve the secret separately, using the role that owns the new integration:

```sql
SELECT SYSTEM$SHOW_OAUTH_CLIENT_SECRETS('STUDIO_MCP_OAUTH');
```

Open the result cell to view the JSON. Copy the value of `OAUTH_CLIENT_SECRET`
directly into Studio's **OAuth client secret** field in B5.
`OAUTH_CLIENT_SECRET_2` is a second valid secret for rotation; use one value,
never the entire JSON object. Its `OAUTH_CLIENT_ID` must match the ID obtained
above.

Preserve characters such as `+`, `/` and `=`. Omit the surrounding JSON quotes
and do not URL-encode either value. This secret belongs to the OAuth application;
it is not the person's Snowflake password or a PAT. Keep it out of source files,
agent instructions, screenshots and shared notes.

<a id="create-the-aws-oauth-provider-in-studio"></a>

### B5. Enter the server and create a new provider in Studio

Sign in to Studio and switch to **Platform Admin**. Open **Platform governance →
MCP servers → Add MCP connection**. If the wizard is already open, continue in it.

In **Step 1: Server**, fill:

| Studio field | Enter | Source |
| --- | --- | --- |
| MCP source | **Connect hosted endpoint** | The server created in B2 runs in Snowflake |
| Connection name | A name you choose, for example `Snowflake reader` | A Studio label; keep the name already entered if continuing |
| Description | For example, `Read permitted Snowflake data with user sign-in` | Optional text |
| MCP endpoint URL | The complete `MCP_ENDPOINT_URL` from B2 | The account host, database, schema and MCP server name |

Choose **Next**. In **Step 2: Authentication**, fill every field as follows:

| Field shown in Studio | Value to enter or select | How you obtain it |
| --- | --- | --- |
| Authentication method | **User sign-in (OAuth 3LO)** | Select this user-consent flow |
| OAuth provider setup | **Create provider** | Creates a new AWS provider using the Snowflake integration from B3 |
| OAuth client ID | The full `OAUTH_CLIENT_ID` value | B4 query result, from `DESCRIBE SECURITY INTEGRATION STUDIO_MCP_OAUTH` |
| OAuth issuer | The full `OAUTH_ISSUER` value, shaped like `https://<snowflake-oauth-host>` | B4 extracts the origin from `OAUTH_AUTHORIZATION_ENDPOINT`; no `/oauth/authorize` suffix |
| Authorization endpoint | The full `AUTHORIZATION_ENDPOINT` value, ending in `/oauth/authorize` | B4 reads `OAUTH_AUTHORIZATION_ENDPOINT` from the integration |
| Token endpoint | The full `TOKEN_ENDPOINT` value, ending in `/oauth/token-request` | B4 reads `OAUTH_TOKEN_ENDPOINT` from the integration |
| Client authentication | **Client secret in POST body** | This is Snowflake's supported `client_secret_post` method |
| OAuth client secret | One `OAUTH_CLIENT_SECRET` value | B4's `SYSTEM$SHOW_OAUTH_CLIENT_SECRETS('STUDIO_MCP_OAUTH')`; paste only the secret into the masked field |
| OAuth scopes | `session:role:MCP_READER refresh_token` | B4's `OAUTH_SCOPES`; replace `MCP_READER` with the exact role you granted and allowed in B1/B3 |

Enter the scopes as one line with a space between them, without quotes or commas.
`session:role:MCP_READER` requests that session role; the grants in B1 determine
which data it can read. `refresh_token` requests renewal after the user consents.

Click **Save OAuth connection** once. Wait for **Authentication saved** and the
**Check the OAuth callback** panel. Studio creates the AWS provider, stores its
secret in Secrets Manager and selects the saved authentication for this MCP.
The authentication name is derived from your connection name, for example
`Snowflake reader OAuth`.

Studio now shows **Existing connection** because it selected the record you just
created. That is the result of saving your new provider. Continue with it.
You do not need to find or enter a provider ARN.

If saving reports an incomplete request, use **Check OAuth connection status**
and the offered recovery control for that request. Do not create another provider
to work around an unknown save status.

**Checkpoint:** authentication is saved. Keep the wizard open at this step and
complete B6 before testing sign-in.

<a id="check-the-oauth-callback"></a>

### B6. Register the generated callback in Snowflake

The generated URL has this shape:

```text
https://bedrock-agentcore.<AWS_REGION>.amazonaws.com/identities/oauth2/callback/<GENERATED_ID>
```

The line above shows the URL's shape only. Copy the real value displayed in
Studio, including its region and generated identifier; do not construct one.

1. In the current Studio **Authentication** step, copy the full URL from
   **Check the OAuth callback**.
2. Return to your Snowflake SQL file. Replace `<EXACT_CALLBACK_FROM_STUDIO>`
   below with that complete URL.
3. Update the **new integration created in B3**, then enable it:

```sql
ALTER SECURITY INTEGRATION STUDIO_MCP_OAUTH
  SET OAUTH_REDIRECT_URI = '<EXACT_CALLBACK_FROM_STUDIO>';
ALTER SECURITY INTEGRATION STUDIO_MCP_OAUTH SET ENABLED = TRUE;
DESCRIBE SECURITY INTEGRATION STUDIO_MCP_OAUTH;
```

Inspect the `DESCRIBE` result:

| Snowflake property | Required result |
| --- | --- |
| `ENABLED` | `true` |
| `OAUTH_REDIRECT_URI` | Exactly the callback copied from this new Studio provider; the temporary value has been replaced |
| `OAUTH_CLIENT_ID` | The same ID entered in B5 |
| `OAUTH_ISSUE_REFRESH_TOKENS` | `true` |
| `OAUTH_USE_SECONDARY_ROLES` | `NONE` |
| `ALLOWED_ROLES_LIST` | Includes `MCP_READER`, or your chosen reader role |

Studio generates this callback, so there is no callback input to fill in Studio.
The return address is entered in Snowflake. After checking the result, return to
the **same Studio wizard** and choose **Next** to open **Tools and workspaces**.

**Checkpoint:** the integration is enabled with the generated callback and the
reader role. Keep the provider you just saved; no second authentication setup
is required.

<a id="publish-the-mcp-connection"></a>

### B7. Load the tool definition and choose workspaces

Query the MCP server in Snowsight, copy its `server_spec` JSON, and paste it
unchanged into Studio. Studio converts supported Snowflake tool definitions
into the format used to invoke them. No additional PAT, OAuth client,
command-line export or manually written input schema is needed for this SQL
server.

#### 1. Read the server configuration in Snowflake

For B2's server, run:

```sql
DESCRIBE MCP SERVER MCP_DEMO.DATA.READ_ONLY_MCP;
```

If you are connecting your own server, replace the fully qualified name with
its database, schema and MCP server name from the endpoint you entered in
Step 1. To find servers in a schema, run:

```sql
SHOW MCP SERVERS IN SCHEMA MCP_DEMO.DATA;
```

Open the `server_spec` cell in the `DESCRIBE` result. Snowflake returns JSON
containing `version` and `tools`. Confirm the tool's `name`, `description` and
`type`. For this walkthrough, its name is `query_sql` and its type is
`SYSTEM_EXECUTE_SQL`.

#### 2. Copy the complete server_spec value

Copy the entire cell value, starting with `{` and ending with `}`. It will look
like this for B2's server; use your actual query result:

```json
{
  "version": 1,
  "tools": [
    {
      "title": "Query permitted Snowflake data",
      "name": "query_sql",
      "type": "SYSTEM_EXECUTE_SQL",
      "description": "Run read-only SQL against objects allowed by the connected Snowflake role. Use fully qualified names and return only the data needed for the question.",
      "config": {
        "read_only": true,
        "warehouse": "MCP_READ_WH"
      }
    }
  ]
}
```

Keep the values Snowflake returns, including your tool name and warehouse. Do
not replace them with the example values above or add an `inputSchema`.

#### 3. Import and review in Studio

1. In **Step 3: Tools and workspaces**, expand **Paste tool definitions instead**
   and paste the complete `server_spec` value. Alternatively, save that same
   value as `snowflake-server-spec.json` and use **Choose tool definitions file**.
2. Expect **Snowflake definition ready**. Studio prepares the SQL inputs
   automatically. Confirm that the preview matches the names and count in your
   query result. For this walkthrough, expect **1 tool definition loaded** and
   `query_sql`.
3. Under **Visible workspaces**, select the workspace where the business user
   will create the agent. This selection controls Studio visibility; it does
   not grant Snowflake data access.
4. Click **Connect and review**. Wait for **Step 4: Review and publish**.

The warehouse and read-only setting remain on the Snowflake server. Importing
the definition does not change Snowflake configuration or permissions.

Studio currently converts version `1` Snowflake specifications containing
`SYSTEM_EXECUTE_SQL` tools. If a definition contains another tool type that
Studio cannot convert, the error names that tool and type. The import is
blocked; Studio does not silently omit tools or guess their arguments.

For invalid JSON, recopy the complete `server_spec` cell rather than the whole
query-results table, a truncated cell preview or the YAML used to create the
server. Duplicate tool names and unsupported specification versions also
disable **Connect and review**.

The import accepts up to 180,000 UTF-8 bytes and 1–100 tools.

#### Other MCP definition formats

Studio also accepts standard MCP definitions in `{"tools":[...]}` and JSON-RPC
responses containing `result.tools`. These formats already include each tool's
`inputSchema`, so Studio validates and uses it directly. Existing definition
files remain supported.

`tools/list` is the MCP protocol's HTTP request for that standard format. It is
not a Snowflake SQL command and is not a required extra step for the
`SYSTEM_EXECUTE_SQL` walkthrough above.

Hosted 3LO still has no pre-publication **Sign in and discover tools** action.
This change imports the definition you supply; B10's real agent test verifies
the tool and the user's data permissions.

### B8. Review and publish the connection

1. Check that the review lists your connection and `query_sql`.
2. Select `query_sql` for publication.
3. Click **Approve and publish**.
4. Wait for **Connection published**, then choose **Done**.
5. In **Registered MCP connections**, confirm the connection has status **Ready**.

**Checkpoint:** your MCP connection and its tool are published in the chosen
workspace. The next steps verify the user's Snowflake consent and data access;
publication alone does not exercise them.

There is no fixed 35-entry workspace catalog cap. The catalog stores the server
and its tools separately; **Registry & AI Catalog** can show one server row with
its tools under **Tools exposed by this MCP connection**. Onboarding supports
reviewing up to 100 tools and selecting up to 20 for publication.

<a id="create-and-test-an-agent"></a>
<a id="create-the-agent-as-a-business-user"></a>

### B9. Create the agent as a business user

1. Switch to **Business User** and the workspace where the MCP was published.
2. Open **AI Catalog** and confirm the model and MCP are available. Use **Request
   access** if required; an authorized approver handles it in **Policies &
   approvals**.
3. Open **Create agent**, select a suitable available template, such as
   **Knowledge Q&A**, and click **Next**.
4. Enter an agent name, for example `Snowflake reader test`, and select an
   approved tool-capable **Model**.
5. Under **MCP servers**, select the connection you published in B8. Expand
   **Tool permissions** and allow `query_sql`. For this first test, remove
   unrelated MCP selections or discovery skills inherited from the template.
6. Use these initial instructions, substituting the intended database/schema:

```text
Use the selected Snowflake MCP tools for data in MCP_DEMO.DATA.
For a request with an explicit tool and arguments, call that tool once.
For other requests, use only the tools needed and ask for missing inputs.
Use read-only queries and fully qualified table names.
Report actual tool results, including errors or truncation.
Request user authorization when required. Never invent a successful result.
```

7. Click **Next**. Leave **Evaluation dataset JSON** empty for this manual test,
   then continue to review. Confirm the model, this MCP connection, `query_sql`
   permission and instructions, then click **Deploy to AgentCore**.
8. Wait for **Agent overview → Deployment → Deployed**, then open **Chat**.

For an existing agent, use **Revise agent**, select the intended connection and
tools, then deploy the new version. Publishing a connection does not change tools
already pinned to a saved agent version.

<a id="run-the-tests-in-chat"></a>

### B10. Complete Snowflake sign-in and test through Chat

In **Chat**, put the first prompt below in **Your question**, then click
**Send message**:

```text
Call query_sql exactly once with:
{"sql":"SELECT CURRENT_USER() AS USER_NAME, CURRENT_ROLE() AS ROLE_NAME, CURRENT_WAREHOUSE() AS WAREHOUSE_NAME"}
Return the actual result. If it fails, report the error.
```

When Studio opens Snowflake sign-in:

1. Use the same browser profile that started the request, particularly if it
   holds your passkey. If the popup is blocked, choose **Open provider sign-in**.
2. Sign in as the Snowflake user granted `MCP_READER` in B1.
3. Review the new integration name and requested access, then approve consent.
4. Return to Studio. Wait while the pending question continues. If Studio
   explicitly asks you to resubmit, review the question and send it once.

The first result should show the consenting Snowflake username, `MCP_READER`
and `MCP_READ_WH` for this example. A valid saved authorization can avoid another
popup on later requests.

After the identity test succeeds, send this separate question:

```text
Call query_sql exactly once with:
{"sql":"SELECT TABLE_NAME FROM MCP_DEMO.INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'DATA' ORDER BY TABLE_NAME"}
Return the actual result. If it fails, report the error.
```

Expected: the table list includes `MCP_SETUP_CHECK`.

Then send this separate question:

```text
Call query_sql exactly once with:
{"sql":"SELECT COUNT(*) AS ROW_COUNT FROM MCP_DEMO.DATA.MCP_SETUP_CHECK"}
Return ROW_COUNT and the Snowflake query ID supplied by the tool.
If it fails, report the error.
```

Expected: `ROW_COUNT = 2` for a fresh sample table, or the independently known
count for your own data. Substitute your object names in the prompts if you
used different names.

After each run, expand **Execution details → Gateway tools called**. Expect one
`query_sql` call without an `(error)` marker, the expected identity or data, and
no **Run failed**. Gateway tool names include a connection-specific prefix.
In Snowsight **Query History**, locate the returned query ID and confirm the SQL
succeeded under the expected user and role. Reload the Studio agent page and
check that the saved conversation is still visible.

For a new 3LO rollout, repeat the identity test as a second approved user; it
should return that person's identity. An administrator can also arrange a
read-only query against a known object outside the reader role's grants and
confirm access is denied.

If any test fails, keep the connection and agent, record the displayed error and
trace/request ID, and use [Troubleshooting](#troubleshooting). Changing object
names or creating another provider does not diagnose the failed step.

## A. Managed MCP with PAT

PAT authentication uses one service user's privileges for every caller of this
Studio connection. Users do not receive a Snowflake sign-in prompt.

First complete [B1's sample-data and reader-role setup](#prepare-snowflake-access),
then [B2's managed server and endpoint setup](#get-a-snowflake-managed-mcp-endpoint).
For this path, grant the role to the service user created below; a personal
user grant is not needed. Skip B3–B6, which create user OAuth.

### A1. Create the service user and PAT

For a new service identity:

```sql
CREATE USER MCP_SERVICE_USER TYPE = SERVICE
  DEFAULT_ROLE = MCP_READER DEFAULT_WAREHOUSE = MCP_READ_WH;
GRANT ROLE MCP_READER TO USER MCP_SERVICE_USER;

CREATE AUTHENTICATION POLICY MCP_DEMO.DATA.MCP_PAT_POLICY
  AUTHENTICATION_METHODS = ('PROGRAMMATIC_ACCESS_TOKEN')
  PAT_POLICY = (NETWORK_POLICY_EVALUATION = ENFORCED_NOT_REQUIRED
                DEFAULT_EXPIRY_IN_DAYS = 30 MAX_EXPIRY_IN_DAYS = 30);
ALTER USER MCP_SERVICE_USER
  SET AUTHENTICATION POLICY MCP_DEMO.DATA.MCP_PAT_POLICY;

ALTER USER MCP_SERVICE_USER
  ADD PROGRAMMATIC ACCESS TOKEN STUDIO_MCP_PAT
  ROLE_RESTRICTION = 'MCP_READER' DAYS_TO_EXPIRY = 30;
```

Copy `token_secret` from the final result directly into Studio's masked field.
It is shown once. `SHOW USER PROGRAMMATIC ACCESS TOKENS FOR USER MCP_SERVICE_USER;`
shows metadata, not the secret. This service user's PAT will not appear in your
own user's personal token list.

The sample policy applies only to this new service user. `ENFORCED_NOT_REQUIRED`
allows a PAT without requiring a network policy; any applicable network policy
is still enforced. Gateway uses managed egress, so allowing your laptop's IP
does not allow Gateway. If your organization requires fixed network rules, use
its approved connectivity and authentication policy instead. Inspect an
existing service user's policy before changing it.

### A2. Save authentication in Studio

1. Open **Platform governance → MCP servers → Add MCP connection**.
2. Choose **Connect hosted endpoint**. Enter a connection name, description and
   the [managed MCP URL](#get-a-snowflake-managed-mcp-endpoint). Choose **Next**.
3. In **Authentication**, select **API key / PAT** and fill:

| Field | Value |
| --- | --- |
| Header name | `Authorization` |
| Header prefix | `Bearer` |
| API key or PAT | The `token_secret` returned when creating the PAT; do not include `Bearer` in the secret |

4. Choose **Save authentication** and wait for **Authentication saved**.
5. Choose **Next** to open **Tools and workspaces**. Select **Visible workspaces**,
   then **Connect and discover**. PAT authentication discovers the server's
   tools automatically; no JSON import is needed.
6. In **Review and publish**, select `query_sql`, choose **Approve and publish**,
   wait for **Connection published**, then choose **Done**.
7. Follow [B9](#create-and-test-an-agent) to create an agent with this connection
   and run the three `query_sql` prompts in [B10](#run-the-tests-in-chat).
   There is no Snowflake consent popup. The identity query should return
   `MCP_SERVICE_USER` and `MCP_READER`; a fresh sample table has two rows.

Gateway stores the credential through Secrets Manager and injects the bearer
header on outbound requests. The agent prompt does not contain the PAT.

## C. Uploaded Python MCP with user sign-in (3LO)

This path deploys your own server code. No `CREATE MCP SERVER` statement is
needed in Snowflake. The package supplies seven read-only tools:
`test_connection`, `list_databases`, `list_schemas`, `list_tables`, `list_views`,
`describe_table` and `query`.

Complete [B1](#prepare-snowflake-access) for the sample data and the consenting
user's reader-role grant. Skip B2 and continue below.

### C1. Build the complete package

Obtain the repository and install [uv](https://docs.astral.sh/uv/getting-started/installation/).
In [main.py](../examples/runtime-snowflake-mcp/main.py), edit these three
non-secret constants using the values from [Prepare access](#prepare-snowflake-access):

```python
SNOWFLAKE_ACCOUNT = "your-organization-your-account"
SNOWFLAKE_ROLE = "MCP_READER"
SNOWFLAKE_WAREHOUSE = "MCP_READ_WH"
```

Keep the supporting modules and dependency lock. From the repository root:

```bash
cd examples/runtime-snowflake-mcp
uv sync --locked
uv run --locked python package.py --output artifacts/runtime.zip
uv run --locked python export_schema.py --output artifacts/tools.json
```

Upload **`examples/runtime-snowflake-mcp/artifacts/runtime.zip`**. The builder
includes root `main.py`, `mcp-package.json`, supporting Python modules and locked
third-party dependencies for Python 3.13 on Linux ARM64. Studio does not install
`requirements.txt` during upload. Do not ZIP a containing folder or upload
`main.py` alone. Credentials do not belong in the package.

The server must serve Streamable HTTP on `0.0.0.0:8000/mcp`. Upload limits are
64 MiB compressed, 256 MiB expanded and 20,000 entries. This example's `query`
tool accepts one read-only SELECT, returns up to 200 rows / 64 KiB and uses a
20-second Snowflake statement timeout. These are tool implementation limits.

### C2. Upload once and use the deployed package

1. Open **MCP servers → Add MCP connection → Upload MCP package (.zip)**.
2. Enter a connection name, choose `runtime.zip`, then click **Upload and deploy
   package**.
3. Wait for **READY**, then click **Use this MCP package**. Studio fills
   **MCP endpoint URL** and retains the package's discovered tool definitions.
4. Choose **Next** to open **Authentication**, then select **User sign-in
   (OAuth 3LO)**. **Server endpoint** changes to the package's HTTPS bearer URL,
   usually containing `/mcp/…`. Keep that supplied address.
5. Follow [B3–B6](#configure-user-sign-in) to create a new Snowflake integration,
   retrieve its values, choose **Create provider**, save and register the
   generated callback. In B5, stay in your current **Authentication** step.
   Keep the uploaded package's endpoint; use the same Snowflake OAuth field
   mapping. You have already completed **Server**.
6. Choose **Next**. In **Tools and workspaces**, expect **Tools loaded from your
   package** with seven tools; skip the managed server's one-tool JSON.
7. Select **Visible workspaces** and click **Connect and review**. In **Review
   and publish**, select `test_connection`, `list_tables` and `query` for the
   tests below, then **Approve and publish**.
8. Wait for **Connection published**, then choose **Done**.

If the package is already ready, choose **Use a deployed package** in **Server**,
select it under **Saved MCP package deployments**, then click **Use this MCP
package → Next**. This reuses the server. If upload was interrupted, choose
**Upload MCP package (.zip)**, then **Check package status** and the offered
resume/reconcile action for that saved request.

### C3. Create an agent and test the Python tools

Follow [B9](#create-and-test-an-agent), selecting your Python MCP connection.
Allow `test_connection`, `list_tables` and `query` under **Tool permissions**
instead of `query_sql`. Use the same read-only agent instructions, deploy and
open **Chat**.

Send each prompt separately. Complete Snowflake sign-in and consent as described
in [B10](#run-the-tests-in-chat) when requested.

```text
Call test_connection exactly once with no arguments. Return the actual result.
```

Expected: the consenting person's Snowflake identity, `MCP_READER` and
`MCP_READ_WH`, or the values configured in your package.

```text
Call list_tables exactly once with database="MCP_DEMO" and schema="DATA".
Return the actual result.
```

Expected: `MCP_SETUP_CHECK` appears when using the sample.

```text
Call query exactly once with:
{"sql":"SELECT COUNT(*) AS ROW_COUNT FROM MCP_DEMO.DATA.MCP_SETUP_CHECK","max_rows":1}
Return the actual result. If it fails, report the error.
```

Expected: `ROW_COUNT = 2` for the fresh sample. Check **Execution details** for
the intended tool and no error marker, compare the query with Snowsight
**Query History**, and reload the agent page to confirm the saved result.

## Continue an existing setup

Use this section when you have already completed part of the journey. A new
user can finish the B1–B10 walkthrough without using any existing AWS provider.

| What you already have | Continue here |
| --- | --- |
| A Snowflake-managed MCP server | Inspect it with `DESCRIBE MCP SERVER <DATABASE>.<SCHEMA>.<MCP_SERVER>;`, check grants and construct its URL using B2. For a new OAuth integration and provider, start at B3. |
| An uploaded package marked **READY** | Choose **Use a deployed package**, select it and click **Use this MCP package → Next**. Continue at C2 step 4. |
| A Snowflake OAuth integration, but you want a new AWS provider | Retrieve its client details in B4 and choose **Create provider** in B5. Check the existing integration's allowed callback as described below. |
| An AWS OAuth provider you intentionally want to reuse | Use [Existing provider](#reuse-an-aws-provider-or-saved-authentication). |
| Authentication just saved in the wizard | Complete its callback setup if needed, then **Next**. Studio already selected it. |
| A saved authentication row marked **Unused** outside the wizard | Follow [Continue after saving authentication](#continue-after-saving-authentication). |
| A published MCP connection | [Create and test an agent](#create-and-test-an-agent). |
| A registered MCP whose authentication needs changing | Read [Change or remove a connection](#change-or-remove-a-connection) before creating another registration. |

### Check the callback for an existing integration

Use `DESCRIBE SECURITY INTEGRATION <INTEGRATION_NAME>;` to confirm the client is
confidential, enabled, issues refresh tokens and allows the intended reader role.
Compare its `OAUTH_REDIRECT_URI` and `OAUTH_ALTERNATE_REDIRECT_URIS` with the
callback belonging to the AWS provider used by this connection.

| Where the provider was saved | Where to copy its callback |
| --- | --- |
| Still in **Authentication** after **Create provider** | **Check the OAuth callback** panel |
| A provider created by Studio, after leaving the wizard | **Manage saved authentication → Refresh credentials → select its original row → OAuth callback URL** |
| An **Existing provider** reference with no callback panel | The provider's details in **AWS console → Bedrock AgentCore → Identity**, in the Studio account and region; ask the platform administrator if needed |

If the exact callback is already allowed, continue without changing Snowflake.
If it is missing from an integration used by other connections, preserve its
primary callback and set the alternate list to **all existing alternates plus
the new callback**:

```sql
ALTER SECURITY INTEGRATION <EXISTING_INTEGRATION>
  SET OAUTH_ALTERNATE_REDIRECT_URIS = (
    '<EXISTING_ALTERNATE_TO_KEEP>',
    '<NEW_CALLBACK_FROM_STUDIO>'
  );
```

Replace the list with actual values. If it was empty, include only the new
callback. `SET` replaces the list; it does not append. Read back
`DESCRIBE SECURITY INTEGRATION` and verify the complete list.

Two AWS accounts can use the same Snowflake integration when both providers'
actual callbacks are allowed. Each AWS environment needs its own compatible
provider and saved authentication. Separate integrations can isolate their
configuration and credentials.

### Reuse an AWS provider or saved authentication

| Choice | What it reuses | What to supply |
| --- | --- | --- |
| **OAuth provider setup → Existing provider** | An AgentCore OAuth provider already installed for this Studio environment | Its **OAuth provider ARN** and the role/refresh scopes |
| **Authentication method → Existing connection** | A saved Studio authentication row | Select the row for this exact MCP endpoint; do not enter its secret again |

For **Existing provider**, obtain the ARN from your platform administrator or
the provider's details in **AWS console → Bedrock AgentCore → Identity**, using
the Studio AWS account and region. Studio verifies the provider's configuration,
deployment ownership and external Secrets Manager reference. An ARN from another
AWS account or an arbitrary provider is not sufficient.

Saving an **Existing provider** reference does not create a provider or change
its callback. Check that provider's existing callback using the section above.
If it already works with this Snowflake integration, no callback update is needed.
If Studio says authentication already exists for this endpoint, choose
**Existing connection** and its saved row instead of adding another reference.

### Continue after saving authentication

An authentication row marked **Unused** is saved but is not attached to a
registered MCP connection. The blue message **Authentication is bound to this
endpoint. Delete an unused reference to replace it.** describes edit restrictions;
it does not mean setup failed.

1. If you have not checked the callback, finish B6 for a new integration or
   [check the existing integration](#check-the-callback-for-an-existing-integration).
   An already allowed callback needs no Snowflake update.
2. If you saved OAuth in the wizard, Studio has already selected it. Choose
   **Next** to open **Tools and workspaces**.
3. If authentication was saved separately, open **Add MCP connection**. In
   **Server**, select the hosted endpoint or use the ready uploaded package,
   then choose **Next**. In **Authentication**, choose **Existing connection**,
   select your saved row and choose **Next**. Do not save another OAuth provider.
4. For a hosted 3LO server, continue with [B7–B8](#publish-the-mcp-connection).
   For a Python package, C2 steps 6–8 use its loaded definitions.

If this exact endpoint already appears under **Registered MCP connections**, use
that registration or follow [Change or remove a connection](#change-or-remove-a-connection).
One Studio installation permits one active MCP registration per exact endpoint;
choosing a new name does not create a second registration for it.

## Troubleshooting

| What you see | What it means and what to do |
| --- | --- |
| The Create provider fields are empty and you have no provider | Create the Snowflake integration in B3, retrieve its values in B4, then fill the B5 table. A new AWS provider does not require an ARN. |
| **Save OAuth connection** is disabled | Fill all the B5 fields, including the masked secret and scopes, and confirm the Server step has a connection name and endpoint. |
| There is no callback URL input in Studio | Expected. Save the new provider, copy **Check the OAuth callback**, then complete B6 in Snowflake. |
| **Paste a JSON object containing the server's tools array** | Follow B7: run `DESCRIBE MCP SERVER`, copy the complete `server_spec` JSON cell, and paste it unchanged. Include the outer `version` and `tools` fields; do not paste the whole results grid or add an `inputSchema`. |
| Authentication is **Unused**, with a blue endpoint-binding message | Saving succeeded. If still in the wizard, complete the callback and choose **Next**. If it was saved separately, [continue here](#continue-after-saving-authentication). |
| **Edit authentication** is disabled | OAuth details are not editable in place through this form. Reuse the row for onboarding; replacement follows the dependency rules below. |
| **Authentication already exists for this endpoint** when referencing a provider | Select its saved authentication through **Existing connection**. |
| **This connection name or endpoint has already been onboarded** | Find the existing registration. A different label does not permit a second active registration for the same endpoint. |
| OAuth redirect URI mismatch | Compare Studio's displayed callback with the integration's primary and alternate redirect URIs. Register the exact value, preserving other active callbacks. Changing the MCP endpoint or OAuth issuer cannot fix this mismatch. |
| Sign-in succeeds but tools report missing privileges | Check the actual user's role membership, `ALLOWED_ROLES_LIST`, scope, warehouse, database/schema/table grants and managed-server `USAGE`. An administrator's successful worksheet query does not prove the user's access. |
| No default warehouse / warehouse access error | For managed MCP, inspect the user's `DEFAULT_WAREHOUSE` and warehouse `USAGE`; also check the tool's configured warehouse. For Python, check the package constant and reader grants. |
| OAuth save reports an incomplete request | Use **Check OAuth connection status** for the retained request first. Follow its explicit recovery action; do not repeatedly create new providers. |
| **Sign-in expired**, or a callback reports a missing `cookie` | The authorization request lasts ten minutes. Check the saved request, return to Chat and send the intended test once to obtain a fresh link. Use that link in the same browser profile; do not reload an old callback URL. Escalate a repeated error on a fresh request. |
| No new Snowflake popup | A valid authorization may already exist. Run the identity test; no popup alone is not a failure. |
| ZIP rejected or **NEEDS_RECONCILIATION** | Check the saved package status and specific validation detail. Rebuild with the supplied builder for root `main.py`, dependencies and target platform. Reconcile the existing request before creating another deployment. |
| Many tool calls for “list all tables” | The model may enumerate databases and schemas to infer scope. Use the explicit one-schema test above to verify the connection, then give the agent the intended scope and tool-selection instructions. |
| `RuntimeClientError` or another **Run failed** | Preserve the request/trace ID and inspect the tool error in **Execution details**. Check identity and a single permitted-table query before attempting a broad task. Report the real error to the platform administrator. |

## Change or remove a connection

Check **Used by** and saved-agent dependencies before changing authentication
or a registration. Existing users may depend on them.

| Action | Portal steps and effect |
| --- | --- |
| Switch an unused MCP from PAT to 3LO | Create the OAuth authentication and check that its callback is already allowed; add it only if missing. Select the existing MCP under **Registered MCP connections → Edit connection**, choose the new saved authentication, supply its schema, then **Save and review → Approve and publish**. Keep the same endpoint. |
| Change a connection referenced by saved agent versions | Editing is blocked. Review the displayed dependencies and arrange an explicit migration before removing them. Do not delete users' agents or create duplicate registrations as a workaround. |
| Replace an unused OAuth authentication | Create the replacement and attach it where permitted, then delete only the unused old row. For an existing-provider reference that hits the duplicate-endpoint check, remove the unused old reference before saving its replacement. |
| Rotate a PAT | Issue a new role-scoped PAT in Snowflake. Studio supports editing an unused PAT authentication; an in-use connection must follow the displayed dependency restrictions. Test the replacement before revoking the old PAT. |
| Remove an unused registration | **Registered MCP connections → Delete connection**, then type its name to confirm. This removes Gateway/Registry/catalog bindings; it does not drop Snowflake objects or automatically delete saved authentication. |
| Remove unused authentication | **Manage saved authentication → select the row → Delete authentication**. Read the confirmation: a Studio-owned provider and its secret have a different lifecycle from a reference to an externally managed provider. |
| Remove a Python deployment | Remove its unused registration first, then **Manage uploaded MCP servers → select the deployment → Delete MCP deployment**. This removes the runtime/code, not Snowflake data. |

In Snowflake, revoke an old PAT with
`ALTER USER MCP_SERVICE_USER REMOVE PROGRAMMATIC ACCESS TOKEN <OLD_PAT_NAME>;`.
To stop an OAuth integration for all its users, an administrator can run
`ALTER SECURITY INTEGRATION STUDIO_MCP_OAUTH SET ENABLED = FALSE;`.
Removing one user's reader-role grant affects that user's access instead.

## Other authentication and tool choices

**OAuth 2LO:** Studio's **Service credentials (OAuth 2LO)** is a separate
client-credentials flow with no individual sign-in. The native Snowflake OAuth
integration created above supports authorization-code 3LO; selecting 2LO in
Studio does not make it accept client credentials. A Snowflake 2LO design needs
a compatible external OAuth provider, Snowflake external OAuth integration and
role/token mapping. This guide does not claim that configuration is established
by the native 3LO recipe.

**AWS IAM / workload identity federation:** IAM can protect an AgentCore MCP
runtime. It does not by itself authorize a query in Snowflake. A workload identity
federation design uses a configured Snowflake service identity and trust
relationship; it is separate from the per-user Python package described here.

**Additional managed tools:** a native Snowflake MCP can expose more than SQL.
Authentication selects the caller's identity; the server definition selects its
tools, and Snowflake grants control their underlying access.

| Snowflake tool type | Purpose |
| --- | --- |
| `SYSTEM_EXECUTE_SQL` | SQL queries and metadata discovery |
| `CORTEX_ANALYST_MESSAGE` | Generate SQL using a semantic view; execute the returned SQL before reporting numerical results |
| `CORTEX_SEARCH_SERVICE_QUERY` | Search indexed documents |
| `CORTEX_AGENT_RUN` | Delegate a task to a Snowflake Cortex Agent |
| `GENERIC` | Expose a supported function or stored procedure |

<details>
<summary>Optional synthetic sales demo with all five tool types</summary>

The companion [sales-demo.sql](../examples/snowflake-managed-mcp/sales-demo.sql)
preserves the larger example without making it part of basic onboarding. Inspect
its object names and feature availability before running it in a new demo
namespace. It creates tables, a semantic view, Search service, function, Cortex
Agent and a six-tool `SALES_MCP` server, including the `sales_sql` compatibility
alias.

Use its role `STUDIO_MCP_EXAMPLE_READ` and warehouse `STUDIO_MCP_EXAMPLE_WH` in
the PAT or 3LO recipe above. Grant the role to the intended service user or
consenting people. The endpoint path is
`/api/v2/databases/STUDIO_MCP_EXAMPLE/schemas/DEMO/mcp-servers/SALES_MCP`.
PAT onboarding discovers its schema; 3LO requires the actual six-tool schema,
not the one-tool JSON supplied for the basic example.

Cortex feature/model availability depends on the Snowflake account and region.
Search must finish initial indexing. Search callers can access content indexed
under the service owner's permissions, so index only the intended dataset.
The script's Cortex orchestration and SQL timeouts are specific to this demo.

For the unmodified fixture, September 2026 revenue is ANZ `7200`, APAC `5400`,
Europe `4800`: total `17400` across nine transactions. A discount quote for `1000`
at `12%` returns net amount `880` and requires approval under document `POL-002`.
Test each selected capability through agent Chat using its discovered schema.

</details>

<details>
<summary>Operator appendix: host the Python package in separately managed AWS stacks</summary>

Use this appendix only when an operator manages the runtime outside Studio's ZIP
upload. End-user setup remains the same: connect the resulting HTTPS endpoint,
save OAuth, publish and test through the portal.

Build the package and `tools.json` using [C1](#c1-build-the-complete-package).
Run the example's tests with `uv run --locked pytest -q`. From
`examples/runtime-snowflake-mcp`, create a private local
`artifacts/runtime-config.json`, replacing all environment values:

```json
{
  "prefix": "example-snowflake",
  "account": "123456789012",
  "profile": "your-aws-profile",
  "region": "us-east-1",
  "gateway_role_arn": "arn:aws:iam::123456789012:role/your-studio-gateway",
  "snowflake_account": "your-organization-your-account",
  "snowflake_role": "MCP_READER",
  "warehouse": "MCP_READ_WH",
  "auth_source": "gateway"
}
```

The prefix is 3–30 lowercase letters, digits or hyphens and starts with a letter.
Use the intended Studio account, region and actual Gateway role. The runtime
receives the user's Snowflake token from Gateway; it does not need a second OAuth
client inside its package.

Review the synthesized templates, IAM permissions and security/tag checks before
deploying the isolated stacks:

```bash
uv run --locked python deploy.py plan \
  --config artifacts/runtime-config.json --state-dir artifacts/deployment
uv run --locked python deploy.py deploy \
  --config artifacts/runtime-config.json --state-dir artifacts/deployment \
  --package artifacts/runtime.zip
uv run --locked python verify.py \
  --state artifacts/deployment/state.json --output artifacts/protocol-verification.json
uv run --locked python facade_deploy.py plan \
  --runtime-state artifacts/deployment/state.json --state-dir artifacts/facade
uv run --locked python facade_deploy.py deploy \
  --runtime-state artifacts/deployment/state.json --state-dir artifacts/facade
```

Wait for successful stacks and audit the deployment with `deploy.py audit` using
the same config/state, and `facade_deploy.py audit` using the same runtime/state
arguments. Verify `auto-delete=no`, private encrypted artifact storage and
authenticated invocation on the actual resources. The runtime's public network
mode provides outbound connectivity; inbound runtime invocation still requires
IAM. The HTTPS adapter rejects requests without a valid provider token.

Use `endpoint` from `artifacts/facade/state.json` in Studio's **Connect hosted
endpoint**, follow [Configure user sign-in](#configure-user-sign-in), and paste
`artifacts/tools.json`. Protocol verification checks discovery and rejection
boundaries; it does not replace the live user-consent/query tests in Chat.

For existing operator-managed OAuth providers, preserve the original config and
`artifacts/oauth` state. `oauth_setup.py audit --config <CONFIG_PATH> --state-dir
<OAUTH_STATE_DIR>` checks that deployment; `configure` uses hidden client-ID and
secret prompts when an authorized update is needed. Its `oauth` settings refer
to Studio's Cognito issuer/client and deployment prefix, not Snowflake's issuer
or client ID. The provider must use the Studio-compatible external secret and
deployment tags before **Existing provider** can reference it.

Preserve all deployment state, request IDs and version receipts. Re-run uncertain
operations with the same inputs to reconcile before attempting a new request.
Use `deploy.py deploy --new-release` only for an intentional package update.
Use `--retry-upload` only after reconciliation and a fresh storage check confirm
the upload is absent. Review retained code, environment and IAM before rollback.
Older WIF/runtime-owned OAuth deployments need an explicit `migrate-gateway`
operation against their existing state before a new gateway-token release.
Delete unused Studio bindings before retiring separately managed stacks;
retained artifacts, logs and secrets require deliberate cleanup.

</details>

## References

- [Snowflake-managed MCP servers](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-mcp)
- [Snowflake custom OAuth clients](https://docs.snowflake.com/en/user-guide/oauth-custom)
- [CREATE SECURITY INTEGRATION for Snowflake OAuth](https://docs.snowflake.com/en/sql-reference/sql/create-security-integration-oauth-snowflake)
- [DESCRIBE SECURITY INTEGRATION](https://docs.snowflake.com/en/sql-reference/sql/desc-integration)
- [Retrieve OAuth client secrets](https://docs.snowflake.com/en/sql-reference/functions/system_show_oauth_client_secrets)
- [Programmatic access tokens](https://docs.snowflake.com/en/user-guide/programmatic-access-tokens)
- [Authentication policies](https://docs.snowflake.com/en/sql-reference/sql/create-authentication-policy)
