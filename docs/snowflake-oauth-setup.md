# Set up Snowflake and obtain the values for Studio

Follow this guide to find your Snowflake account information, prepare read access,
create an OAuth client and obtain the values to paste into Studio. It includes
the SQL to run and the result columns to copy.

The worked path uses the repository's Python Snowflake MCP package and per-user
OAuth 3LO. The MCP server runs in Studio; the data, warehouse, reader role and
OAuth integration belong to Snowflake. For a server hosted natively inside
Snowflake, use the [managed MCP server guide](snowflake-managed-mcp.md) for server
creation and permissions.

Service OAuth 2LO needs a provider and Snowflake configuration that support client
credentials. Selecting 2LO in Studio does not enable that grant in a native
Snowflake OAuth client.

This procedure creates a separate OAuth integration for your Studio setup.
Choose an unused name and keep existing integrations unchanged. The same
Snowflake account and data can be used by several integrations.

## Start here

| Your situation | Where to start |
| --- | --- |
| You have Snowflake access but no MCP setup | Step 1, including the role and warehouse setup |
| Your server already has an approved account, role and warehouse | Confirm those values in step 1, then create the new OAuth integration in step 2 |
| You have created the new integration but need the Studio field values | Step 3 prints the client ID, issuer, endpoints and scopes |
| You have saved OAuth in Studio and need to configure the callback | Step 5 |

You need an existing Snowflake account. A Snowflake administrator performs the
object creation and grants below; a Studio administrator role does not grant
Snowflake administration rights. No existing OAuth integration needs replacing.

## 1. Prepare Snowflake

### Open a SQL file

1. Sign in to [Snowsight](https://app.snowflake.com/) with the browser profile
   you normally use for Snowflake. Select the intended Snowflake account.
2. Open **+ → SQL File** and ensure the new file is active.
3. Select an authorized setup role in the worksheet's role selector.
   **ACCOUNTADMIN** can perform this example; an organization can delegate
   the necessary privileges instead. The agent will use the reader role.
4. Paste and run each block below separately. Read its result before continuing.

Replace `<PLACEHOLDER>` values before running a block. For new objects, use
ordinary unquoted uppercase names, such as `MCP_READER` for a reader role.
Copy existing names exactly from the query results. Case-sensitive or
special-character names require Snowflake quoting; for example, an email-shaped
username needs double quotes in `GRANT ROLE ... TO USER "USER@EXAMPLE.COM"`.
Use the exact role name in the package and OAuth scope as well.

### Find the account identifier and username

Run:

```sql
SELECT
  CURRENT_ORGANIZATION_NAME() AS ORGANIZATION_NAME,
  CURRENT_ACCOUNT_NAME() AS ACCOUNT_NAME,
  CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME()
    AS MCP_ACCOUNT_IDENTIFIER,
  CURRENT_USER() AS SIGNED_IN_USER;
```

Copy **MCP_ACCOUNT_IDENTIFIER** for the package's `SNOWFLAKE_ACCOUNT` setting.
Copy **SIGNED_IN_USER** for `<SNOWFLAKE_USER>` if you will authorize the agent
with this user. If an administrator is preparing access for someone else, use
that person's Snowflake username instead. A Studio email address is not
necessarily the Snowflake username.

### Find a warehouse and the data to read

Run:

```sql
SHOW WAREHOUSES;
SHOW DATABASES;
```

Read the **name** columns. Choose an approved warehouse and database; record
them as `<WAREHOUSE>` and `<DATABASE>`. To find schemas and tables in that
database, replace the placeholders and run:

```sql
SHOW SCHEMAS IN DATABASE <DATABASE>;
SHOW TABLES IN SCHEMA <DATABASE>.<SCHEMA>;
```

Choose the schema and a table the agent is allowed to read. Record their names
as `<SCHEMA>` and `<TABLE>`. These values come from your account's result rows.

If you need a new warehouse, choose an unused name and have the administrator
run this block instead of selecting an existing one:

```sql
CREATE WAREHOUSE <WAREHOUSE>
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE;
```

This creates a suspended warehouse that resumes for queries and suspends after
60 seconds of inactivity. Record its name for the MCP package.

If you have no suitable test data, you can create a separate demonstration
database, schema and table. Choose unused names for `<DATABASE>` and `<SCHEMA>`;
run this optional block only when you intend to create those objects:

```sql
USE WAREHOUSE <WAREHOUSE>;
CREATE DATABASE <DATABASE>;
CREATE SCHEMA <DATABASE>.<SCHEMA>;
CREATE TABLE <DATABASE>.<SCHEMA>.MCP_SETUP_CHECK (
  ITEM_ID INTEGER,
  ITEM_NAME VARCHAR
);
INSERT INTO <DATABASE>.<SCHEMA>.MCP_SETUP_CHECK
  VALUES (1, 'First example'), (2, 'Second example');
```

For this optional dataset, use `MCP_SETUP_CHECK` as `<TABLE>`. It starts with
two rows. Keep using your selected existing data if you skipped this block.

### Create or verify the reader role

For an already deployed MCP, use the role and warehouse configured in that
server. Changing the requested OAuth role alone does not change its package.
If an approved reader role already exists, skip the creation block and verify
its grants below.

To create a new reader role, choose an unused role name for `<READER_ROLE>`.
The administrator runs:

```sql
CREATE ROLE <READER_ROLE>;
GRANT USAGE ON WAREHOUSE <WAREHOUSE> TO ROLE <READER_ROLE>;
GRANT USAGE ON DATABASE <DATABASE> TO ROLE <READER_ROLE>;
GRANT USAGE ON SCHEMA <DATABASE>.<SCHEMA> TO ROLE <READER_ROLE>;
GRANT SELECT ON TABLE <DATABASE>.<SCHEMA>.<TABLE> TO ROLE <READER_ROLE>;
GRANT ROLE <READER_ROLE> TO USER <SNOWFLAKE_USER>;
```

This grants read access to the chosen table and assigns the role to the person
who will authorize the agent. It does not grant access to every database or
future table. Add other objects only when they belong in the intended access.

Verify both the role's object privileges and the user's membership:

```sql
SHOW GRANTS TO ROLE <READER_ROLE>;
SHOW GRANTS TO USER <SNOWFLAKE_USER>;
```

The role result should include warehouse/database/schema `USAGE` and table
`SELECT`. The user result should include the reader role.

The intended Snowflake user can check the same access in a SQL file:

```sql
USE ROLE <READER_ROLE>;
USE SECONDARY ROLES NONE;
USE WAREHOUSE <WAREHOUSE>;
SELECT CURRENT_ROLE() AS ACTIVE_ROLE, CURRENT_WAREHOUSE() AS ACTIVE_WAREHOUSE;
SELECT COUNT(*) AS ROW_COUNT FROM <DATABASE>.<SCHEMA>.<TABLE>;
```

Expected result: the configured reader role and warehouse, followed by a count
from the selected table. Resolve missing grants here before debugging OAuth.
If someone else will authorize the agent, have that person perform this check
under their own Snowflake user.

### Record the values you will reuse

| Value | What you now have |
| --- | --- |
| Account identifier | `MCP_ACCOUNT_IDENTIFIER` from the first query |
| Snowflake username | The intended authorizing user's Snowflake username |
| Warehouse | The selected or newly created warehouse name |
| Reader role | The verified or newly created role name |
| Database, schema and table | The objects selected or created above |
| New OAuth integration name | A name you choose for the client created in step 2 |

Switch the SQL file back to the authorized setup role before creating the
OAuth integration. The reader role is for data access, not integration setup.

## 2. Create the new integration disabled

Snowflake requires a redirect URI to create a client. Studio needs the client
credentials before AgentCore returns its callback. Create the integration
disabled with a temporary HTTPS URI, then replace that URI before enabling it.

In the SQL below, replace `<TEMPORARY_HTTPS_URL>` with an existing HTTPS URL your
organization controls, such as your Studio home URL. **Use this temporary value
only while `ENABLED = FALSE`; replace it with the generated callback in step 5
before enabling sign-in.** No callback path needs to be created at that temporary
URL.

The final callback is generated by AgentCore and has this form in standard AWS
Regions:

```text
https://bedrock-agentcore.<AWS_REGION>.amazonaws.com/identities/oauth2/callback/<GENERATED_ID>
```

Copy the exact callback shown by Studio in step 5; do not construct it from
this pattern or reuse another environment's URL. If you already have the
generated callback for the Studio authentication connection being configured,
you can use that exact URL in the creation statement instead of the temporary
value.

Choose an unused `<NEW_OAUTH_INTEGRATION>` name. Check it first:

```sql
SHOW SECURITY INTEGRATIONS LIKE '<NEW_OAUTH_INTEGRATION>';
```

If it already exists, inspect it and confirm its ownership and purpose.
Choose another new name if it belongs to an existing setup. Replace the
placeholders and run the creation block once:

```sql
CREATE SECURITY INTEGRATION <NEW_OAUTH_INTEGRATION>
  TYPE = OAUTH
  OAUTH_CLIENT = CUSTOM
  OAUTH_CLIENT_TYPE = 'CONFIDENTIAL'
  ENABLED = FALSE
  -- Temporary setup value: replace with the callback from Studio in step 5.
  OAUTH_REDIRECT_URI = '<TEMPORARY_HTTPS_URL>'
  OAUTH_ALLOW_NON_TLS_REDIRECT_URI = FALSE
  OAUTH_USE_SECONDARY_ROLES = NONE
  OAUTH_ISSUE_REFRESH_TOKENS = TRUE
  ALLOWED_ROLES_LIST = ('<READER_ROLE>')
  COMMENT = 'Per-user Snowflake OAuth for this Studio installation';
```

Expected result: the new integration exists and is disabled. The temporary URL
is only a setup placeholder. Do not start provider sign-in until step 5.

This example requests one primary reader role and no secondary roles, matching
the repository's Snowflake MCP example. Choose token-lifetime settings according
to your organization's policy; omitted settings use Snowflake defaults.

## 3. Retrieve the client details

### Generate a row of values to copy into Studio

Select your warehouse, run `DESCRIBE`, then run the `SELECT` immediately
afterward in the same SQL file/session. Do not run another statement between
`DESCRIBE` and `SELECT`: `LAST_QUERY_ID()` must refer to the description result.
Replace the warehouse, integration and role names.

```sql
USE WAREHOUSE <WAREHOUSE>;
DESCRIBE SECURITY INTEGRATION <NEW_OAUTH_INTEGRATION>;

SELECT
  MAX(CASE WHEN "property" = 'OAUTH_CLIENT_ID'
      THEN "property_value" END) AS OAUTH_CLIENT_ID,
  REGEXP_SUBSTR(
    MAX(CASE WHEN "property" = 'OAUTH_AUTHORIZATION_ENDPOINT'
        THEN "property_value" END),
    '^https://[^/]+'
  ) AS OAUTH_ISSUER,
  MAX(CASE WHEN "property" = 'OAUTH_AUTHORIZATION_ENDPOINT'
      THEN "property_value" END) AS AUTHORIZATION_ENDPOINT,
  MAX(CASE WHEN "property" = 'OAUTH_TOKEN_ENDPOINT'
      THEN "property_value" END) AS TOKEN_ENDPOINT,
  'session:role:<READER_ROLE> refresh_token' AS OAUTH_SCOPES
FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()));
```

Expected result: one row with five columns. Copy those values into the matching
Studio fields in step 4. The query derives the issuer from Snowflake's returned
authorization endpoint, so you do not need to guess the account URL.

If a value is empty, run `DESCRIBE` again and inspect its **property** and
**property_value** columns. Confirm you described the new custom OAuth
integration. Its `OAUTH_CLIENT_ID`, `OAUTH_AUTHORIZATION_ENDPOINT` and
`OAUTH_TOKEN_ENDPOINT` rows are also a direct way to copy the values.

### Retrieve the client secret separately

Run:

```sql
SELECT SYSTEM$SHOW_OAUTH_CLIENT_SECRETS('<NEW_OAUTH_INTEGRATION>');
```

Open the JSON result cell and copy the value of **OAUTH_CLIENT_SECRET** into
Studio's **OAuth client secret** field. Use the value without the property name
or surrounding quotation marks. Preserve characters such as `+`, `/` and `=`.
The integration name is not the client ID or secret.

The result can also contain a secondary secret. You only need one valid client
secret for this connection; use the primary value for this walkthrough.
Keep it out of source files, ZIPs, agent instructions and shared screenshots.

The scopes value is constructed from your reader-role name. Snowflake does not
issue a separate "scope secret" or a scope ARN. The `refresh_token` scope
matches the refresh-token setting enabled when you created the integration.

## 4. Configure the package and save authentication

If the MCP is already deployed with the correct account, role and warehouse,
reuse it. For a new server built from the repository's
[Snowflake package](../examples/runtime-snowflake-mcp/README.md), set these
non-secret values in its `main.py`:

```python
SNOWFLAKE_ACCOUNT = "<MCP_ACCOUNT_IDENTIFIER_FROM_STEP_1>"
SNOWFLAKE_ROLE = "<READER_ROLE>"
SNOWFLAKE_WAREHOUSE = "<WAREHOUSE>"
```

Follow the package README to build the complete deployment ZIP. Run its commands
inside the example folder containing `pyproject.toml`, `uv.lock` and `package.py`.
In Studio, open
**MCP servers → Create MCP connection → Upload MCP package (.zip)**, choose your
connection name and ZIP, then **Upload and deploy package**. Wait for **READY**
and select **Use this MCP package**. For an existing upload, select it under
**Saved MCP package deployments** and use it without uploading again.

Choose **User sign-in (OAuth 3LO)**. Studio fills the MCP endpoint for this
package. This endpoint comes from Studio; the account identifier and OAuth URLs
you obtained in Snowflake are different values.

Copy the outputs from step 3 as follows:

| Studio field | Exact source/value |
| --- | --- |
| **OAuth provider setup** | Select **Create provider** |
| **OAuth client ID** | `OAUTH_CLIENT_ID` column from step 3's five-column result |
| **OAuth issuer** | `OAUTH_ISSUER` column |
| **Authorization endpoint** | `AUTHORIZATION_ENDPOINT` column |
| **Token endpoint** | `TOKEN_ENDPOINT` column |
| **Client authentication** | Select **Client secret in POST body**; Snowflake also supports **HTTP Basic** |
| **OAuth client secret** | `OAUTH_CLIENT_SECRET` value from the separate JSON result |
| **OAuth scopes** | `OAUTH_SCOPES` column |

The role scope requests a Snowflake session using that role. It does not grant
the role to the user. `refresh_token` requests renewable access after consent.
Use a role permitted by the integration and appropriate for data access.

Click **Save OAuth connection** and wait for **Authentication saved**. Studio
creates the AWS credential provider, so you do not need an OAuth provider ARN
before this step.

If saving through **Add authentication connection** instead, enter a friendly
**Authentication name** of your choice and use the full MCP endpoint from Studio
for **Endpoint URL**. Complete the same OAuth fields. After saving, choose that
row through **Existing connection** in the MCP form.

## 5. Register the generated callback and enable the new client

Copy the callback from **Register the OAuth callback** in the MCP form, or use:

**MCP servers → Authentication connections → Refresh credentials → select the
new OAuth row → OAuth callback URL**.

There is no callback input in Studio. AgentCore generates the URL; Snowflake's
integration is where you register it. Use the URL from the new row rather than
one copied from another environment.

This AgentCore URL is the complete final value for `OAUTH_REDIRECT_URI`. It
replaces the entire temporary URL used in step 2. Complete this step before
starting Snowflake sign-in.

Replace both placeholders below and run:

```sql
ALTER SECURITY INTEGRATION <NEW_OAUTH_INTEGRATION>
  SET OAUTH_REDIRECT_URI = '<CALLBACK_URL_FROM_STUDIO>'
      ENABLED = TRUE;

DESCRIBE SECURITY INTEGRATION <NEW_OAUTH_INTEGRATION>;
```

Verify:

| Property | Expected value |
| --- | --- |
| `ENABLED` | `true` |
| `OAUTH_REDIRECT_URI` | The exact callback from the new Studio authentication row |
| `OAUTH_CLIENT_ID` | The same client ID entered in Studio |
| `OAUTH_CLIENT_TYPE` | `CONFIDENTIAL` |
| `ALLOWED_ROLES_LIST` | Your intended reader role |
| `OAUTH_USE_SECONDARY_ROLES` | `NONE` for the supplied MCP example |
| `OAUTH_ISSUE_REFRESH_TOKENS` | `true` |

The temporary redirect must be gone before sign-in. A separate integration
isolates the credentials and callback configuration for this setup. Sharing a
single integration across environments is also possible when all callbacks are
allowed, but that is a separate administration decision.

## 6. Publish the MCP and test an agent

Continue with [review and publication](start-here.md#3-review-tools-and-publish),
then [create an agent](start-here.md#4-create-an-agent). Select the MCP linked to
the new authentication connection.

If using the repository's Snowflake MCP example, run these tests as separate
chat messages, waiting for each result:

| Test | Request | Expected evidence |
| --- | --- | --- |
| Connection and consent | `Call test_connection once and report the actual role and warehouse.` | Complete Snowflake sign-in/consent when requested; the result matches your configured role and warehouse |
| Table listing | `Call list_tables once with database="<DATABASE>" and schema="<SCHEMA>". Return the table names.` | Tables visible to your reader role in the specified scope |
| Data access | `Call query once with max_rows=1 and SQL: SELECT COUNT(*) AS ROW_COUNT FROM <DATABASE>.<SCHEMA>.<TABLE>; Return ROW_COUNT and query_id.` | An actual count and Snowflake query ID |

Replace database, schema and table placeholders with objects you are authorized
to read. The `list_tables` tool requires both database and schema; a request for
all tables across an account can require several discovery calls.

Check **Execution details → Gateway tools called** for the intended tool and
any error marker. Provider consent alone does not prove query access.

Complete provider sign-in promptly; Studio's authorization request expires
after ten minutes. If an old sign-in page returns a missing-cookie error, use
[Recover an expired sign-in](start-here.md#recover-an-expired-sign-in) to obtain
a fresh link from the existing agent. An expired request does not require a
new Snowflake integration or MCP deployment.

For a redirect mismatch, compare the callback and client ID with the new
integration's description. For a permission error, check the user's role
membership and the role's object grants. Preserve the error and trace/request
ID rather than recreating the server.

## References

- [Snowflake: Find your account identifier](https://docs.snowflake.com/en/user-guide/admin-account-identifier)
- [Snowflake: Create a warehouse](https://docs.snowflake.com/en/sql-reference/sql/create-warehouse)
- [Snowflake: Grant object privileges](https://docs.snowflake.com/en/sql-reference/sql/grant-privilege)
- [Snowflake: Describe integration properties](https://docs.snowflake.com/en/sql-reference/sql/desc-integration)
- [Snowflake: Read the previous query result](https://docs.snowflake.com/en/sql-reference/functions/result_scan)
- [Snowflake: Create a custom OAuth integration](https://docs.snowflake.com/en/sql-reference/sql/create-security-integration-oauth-snowflake)
- [Snowflake: Update an OAuth integration](https://docs.snowflake.com/en/sql-reference/sql/alter-security-integration-oauth-snowflake)
- [Snowflake: Retrieve client secrets](https://docs.snowflake.com/en/sql-reference/functions/system_show_oauth_client_secrets)
- [Snowflake: Custom-client authentication and scopes](https://docs.snowflake.com/en/user-guide/oauth-custom)
- [AgentCore: OAuth provider creation and returned callback](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CreateOauth2CredentialProvider.html)
