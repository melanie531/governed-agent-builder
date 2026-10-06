# Snowflake query MCP on AgentCore Runtime with per-user OAuth

This optional server connects directly to Snowflake through its Python connector.
It exposes `test_connection`, `list_databases`, `list_schemas`, `list_tables`,
`list_views`, `describe_table`, and `query`. It is a custom MCP hosted by AWS;
Snowflake's managed Cortex MCP is a separate service.

Agent Studio installation does not deploy this example, connect to Snowflake,
create data, or register its endpoint. An operator explicitly deploys it, and a
Studio administrator manually onboards it through the generic MCP interface.
The independent source is in `examples/runtime-snowflake-mcp/`; run the shell
commands below from that directory.

## Authentication

| Hop | Authentication |
|---|---|
| Browser → Studio | Cognito authorization code with PKCE and a server-side session |
| Studio → agent Runtime | AWS IAM, with private user context |
| Agent Runtime → user OAuth Gateway | The current user's Cognito access JWT |
| Gateway → authenticated MCP endpoint | The user's Snowflake OAuth access token |
| MCP endpoint → MCP Runtime | AWS IAM, with a private Snowflake token header |
| MCP Runtime → Snowflake | That user's Snowflake OAuth access token |

Snowflake native OAuth provides the three-legged authorization-code flow. Okta,
a PAT, and a shared WIF service user are not required. Each person signs in to
Snowflake and consents to the configured read role. They must already hold that
role in Snowflake. `CURRENT_USER()` remains that person's Snowflake identity;
row access and masking policies can continue to distinguish people.

One connection requests one configured role. This example disables secondary
roles and SQL role switching. The role's grants define the accessible objects;
the agent's instructions define its task. Individual OAuth sessions do not grant
permissions beyond the selected Snowflake role.

Gateway uses AgentCore Identity to store and refresh each user's tokens. Its
native `AUTHORIZATION_CODE` target requests consent when a tool needs access.
Studio displays that request and completes its session binding with the current
Cognito user. Snowflake is an outbound provider; it is not a Studio login provider.

The optional MCP endpoint is CloudFront → authenticated HTTP API → IAM invocation
Lambda → Runtime. The API authorizer verifies the supplied Snowflake token and
read role using `CURRENT_USER()` and `CURRENT_ROLE()`. The Runtime receives that
token through an IAM-protected header and creates a fresh Snowflake connection
for each tool call. Neither the endpoint nor Runtime obtains or refreshes tokens.
This adapter is needed because Gateway injects Snowflake's opaque bearer token,
while direct Runtime invocation requires IAM or a supported JWT authorizer.
No token is placed in prompts, tool arguments, catalog records or execution evidence.

## Prerequisites and permissions

- An AWS account/region supporting AgentCore Runtime CodeZip, Gateway MCP
  authorization-code OAuth targets, and AgentCore Identity custom OAuth providers.
- A deployed Studio with generic user OAuth onboarding and a Cognito Gateway,
  its origin, Cognito issuer/client ID, credential deployment prefix, Gateway
  execution-role ARN, and native AI Registry.
- Python 3.12/3.13, `uv`, Node.js for CDK synthesis, and a valid AWS CLI profile.
- AWS deployment permissions for the prefixed CloudFormation stacks, versioned
  private S3 artifacts, Secrets Manager, IAM roles/policies, CloudWatch Logs,
  AgentCore Runtime, API Gateway, Lambda, CloudFront, OAuth credential providers,
  workload identities, and resource tagging. The deployer needs `iam:PassRole` for the
  new Runtime role, restricted to `bedrock-agentcore.amazonaws.com`.
- Permission to attach a separately owned inline policy to the existing Gateway
  role granting invocation of this Runtime and its DEFAULT endpoint.
- A Snowflake administrator with `CREATE INTEGRATION`, `CREATE ROLE`, and the
  ability to grant the existing warehouse/database/schema/table privileges.
  `ACCOUNTADMIN` can perform the setup; a delegated administrator with the
  necessary grants is preferable for routine operation.
- Existing Snowflake users, tables/views, and a warehouse. Account authentication,
  MFA and network policies must allow native OAuth and this Runtime's egress.
  This example does not create users, warehouses or sample data.
- Studio Platform Admin access for onboarding and Business User access for tests.

The Runtime role receives its artifact/log permissions and no Identity token
permissions in Gateway mode. The Gateway role can use deployment-prefixed OAuth
providers. Its request interceptor checks current Studio tool grants before
outbound access. The Studio backend completes consent using the current user's
JWT, the initiating user, exact native session URI and current grants.

## 1. Configure the separate deployment

```bash
uv sync --locked
uv run --locked pytest -q
uv run --locked python package.py --output artifacts/runtime.zip
uv run --locked python export_schema.py --output artifacts/tools.json
```

Create a local `artifacts/config.json`, replacing every example value:

```json
{
  "prefix": "customer-snowflake",
  "account": "123456789012",
  "profile": "customer",
  "region": "us-east-1",
  "gateway_role_arn": "arn:aws:iam::123456789012:role/customer-gateway",
  "snowflake_account": "yourorg-youraccount",
  "snowflake_role": "STUDIO_RUNTIME_MCP_READER",
  "warehouse": "YOUR_EXISTING_WAREHOUSE",
  "oauth": {
    "studio_prefix": "customer-studio",
    "name": "snowflake-runtime",
    "issuer": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_REPLACE",
    "client_id": "replacestudioclientid",
    "origin": "https://replace.cloudfront.net"
  }
}
```

`prefix` is 3–30 lowercase letters, digits or hyphens and starts with a letter.
`studio_prefix` must exactly match Studio's `credential_prefix`. The account,
Runtime and Gateway must be in the same AWS account/region. `snowflake_account`
is the lowercase organization-account identifier, without a URL suffix.
Use the actual Cognito issuer and client from Studio, not a Snowflake client ID.

```bash
uv run --locked python oauth_setup.py plan \
  --config artifacts/config.json --state-dir artifacts/oauth
uv run --locked python oauth_setup.py prepare \
  --config artifacts/config.json --state-dir artifacts/oauth
```

Review the synthesized `oauth-template.json`. This separate CDK stack owns:

- A customer-managed workload named `STUDIO_PREFIX-mcp-users-NAME`.
- A custom OAuth provider named `STUDIO_PREFIX-mcp-oauth-NAME`.
- A secret named `STUDIO_PREFIX/mcp/oauth/NAME`, referenced by the provider
  through an EXTERNAL secret reference.
- An allowed application return URL of `STUDIO_ORIGIN/oauth/callback`.

All resources carry `auto-delete=no`, the project tag and the Studio deployment
tag. Preparation uses an unconfigured OAuth client while obtaining AWS's exact
callback URL. It is not a working Snowflake connection yet.

## 2. Create the Snowflake OAuth integration and read grants

Copy `CallbackUrl` from `artifacts/oauth/state.json`. This is AWS's token-exchange
callback, not Studio's application return URL. Use it exactly in the SQL below.

First inventory your existing objects:

```sql
SELECT CURRENT_ACCOUNT(), CURRENT_USER(), CURRENT_ROLE();
SHOW WAREHOUSES;
SHOW DATABASES;
SHOW SCHEMAS IN DATABASE YOUR_DATABASE;
SHOW TABLES IN SCHEMA YOUR_DATABASE.YOUR_SCHEMA;
SHOW VIEWS IN SCHEMA YOUR_DATABASE.YOUR_SCHEMA;
SHOW SECURITY INTEGRATIONS LIKE 'STUDIO_RUNTIME_MCP_OAUTH';
```

Replace the identifiers, existing username and callback. If the integration
already exists, inspect its owner and configuration; do not replace it blindly.

```sql
USE ROLE ACCOUNTADMIN;

CREATE ROLE IF NOT EXISTS STUDIO_RUNTIME_MCP_READER
  COMMENT = 'Read access for individually authorized Studio users';
GRANT USAGE ON WAREHOUSE YOUR_EXISTING_WAREHOUSE TO ROLE STUDIO_RUNTIME_MCP_READER;
GRANT USAGE ON DATABASE YOUR_DATABASE TO ROLE STUDIO_RUNTIME_MCP_READER;
GRANT USAGE ON SCHEMA YOUR_DATABASE.YOUR_SCHEMA TO ROLE STUDIO_RUNTIME_MCP_READER;
GRANT SELECT ON TABLE YOUR_DATABASE.YOUR_SCHEMA.YOUR_TABLE TO ROLE STUDIO_RUNTIME_MCP_READER;
-- Repeat SELECT grants only for approved tables/views.
GRANT ROLE STUDIO_RUNTIME_MCP_READER TO USER YOUR_EXISTING_USER;
-- Repeat the user grant for each person authorized to use this connection.

CREATE SECURITY INTEGRATION STUDIO_RUNTIME_MCP_OAUTH
  TYPE = OAUTH
  ENABLED = TRUE
  OAUTH_CLIENT = CUSTOM
  OAUTH_CLIENT_TYPE = 'CONFIDENTIAL'
  OAUTH_REDIRECT_URI = 'REPLACE_WITH_EXACT_AWS_CALLBACK_URL'
  OAUTH_ALLOW_NON_TLS_REDIRECT_URI = FALSE
  OAUTH_USE_SECONDARY_ROLES = NONE
  OAUTH_ISSUE_REFRESH_TOKENS = TRUE
  OAUTH_REFRESH_TOKEN_VALIDITY = 3600
  ALLOWED_ROLES_LIST = ('STUDIO_RUNTIME_MCP_READER');

DESCRIBE SECURITY INTEGRATION STUDIO_RUNTIME_MCP_OAUTH;
SHOW GRANTS TO ROLE STUDIO_RUNTIME_MCP_READER;
SHOW GRANTS TO USER YOUR_EXISTING_USER;
```

The one-hour refresh-token validity is a Snowflake setting. Studio requests
3LO once per signed-in Studio session, then reuses that authorization for
subsequent messages and new conversations until its saved consent is one
hour old. A new Studio sign-in session requires fresh consent. The agent
submits its question, then the first Cognito Gateway MCP `tools/call` sets
`oauthCredentialProvider.forceAuthentication=true`. Gateway returns its
native authorization challenge. Studio sends the waiting tab to that sign-in
URL and continues the original question only if no tool completed before the
challenge. Gateway may also request consent if its token is unusable earlier.
The saved `Connected` indicator is not proof that a Snowflake query succeeded.

Privileged Snowflake roles such as ACCOUNTADMIN are blocked for OAuth by default.
Leave that protection enabled. Review privileges inherited through `PUBLIC`.

Run the following separately to retrieve the OAuth client's credentials:

```sql
SELECT SYSTEM$SHOW_OAUTH_CLIENT_SECRETS('STUDIO_RUNTIME_MCP_OAUTH');
```

Use `OAUTH_CLIENT_ID` and `OAUTH_CLIENT_SECRET` from the result. These are
application credentials; each user's consent and access token remain separate.
Do not paste the result into chat, Git, screenshots or deployment JSON. Enter the
values into the hidden prompts:

```bash
uv run --locked python oauth_setup.py configure \
  --config artifacts/config.json --state-dir artifacts/oauth
uv run --locked python oauth_setup.py audit \
  --config artifacts/config.json --state-dir artifacts/oauth
```

The command stores the secret directly in the prefixed Secrets Manager resource,
updates the provider through CloudFormation, and checks its native configuration.
The state directory retains operation IDs and a digest for same-input recovery,
but no client secret or user token.

## 3. Deploy and verify the MCP Runtime and endpoint

Keep the preceding OAuth setup config for provider maintenance. Copy it to
`artifacts/runtime-config.json`, remove the `oauth` object, and add
`"auth_source": "gateway"`. The Runtime does not need the Cognito or OAuth provider
settings: they belong to Studio and Gateway.

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

Review `artifacts-template.json` and `runtime-template.json` before deployment.
They create a versioned encrypted private artifact bucket, scoped execution role,
IAM-authenticated MCP Runtime, 14-day logs and exact Gateway invoke permission.
`networkMode=PUBLIC` supplies managed outbound connectivity; incoming invocation
still requires IAM. PrivateLink deployments need a separately designed VPC path.

The deployment waits for successful stacks, validates the exact artifact version,
and checks actual tags on the Runtime, generated endpoint/identity, role, bucket
and logs. Protocol verification checks seven-tool discovery, anonymous denial,
missing-user denial, and rejection of multiple SQL statements. Live Snowflake
query verification happens through Studio after native Gateway consent. Use the
facade's HTTPS `/mcp` endpoint for onboarding. Anonymous calls are denied by the
API authorizer; the underlying Runtime remains IAM protected.

## 4. Manually onboard in Agent Studio

1. As **Platform Admin**, open **MCP servers → Create MCP connection**.
2. Enter a name/description and `endpoint` from `artifacts/facade/state.json`.
3. Choose **OAuth / per-user consent**. Enter `ProviderArn` from
   `artifacts/oauth/state.json` and the scopes
   `session:role:STUDIO_RUNTIME_MCP_READER refresh_token`.
4. Click **Save OAuth connection**. Studio verifies that the existing provider,
   external secret reference, callback and tags belong to this installation.
5. Paste the contents of `artifacts/tools.json` into **MCP tool schema JSON**, choose the
   workspaces and click **Connect and review**. Native authorization-code targets
   use a supplied schema; onboarding does not require a shared Snowflake login.
6. Review the supplied tools, then **Approve and publish**. Wait for
   **Connection published**. The generic workflow creates the Gateway target and
   native AI Registry record.
7. Switch to **Business User**, create an agent, select this MCP and the allowed
   tools, and use instructions
   such as: “Discover the available data and inspect its columns before writing
   SQL. Use read-only queries. Explain missing access and truncated results.”
8. Deploy the agent; deployment does not require the business user's provider
   consent. In **Chat**, run a harmless metadata question. If this Studio
   session has no current authorization, Studio opens a waiting tab and invokes the agent once.
   Its first Gateway MCP tool call starts fresh provider authorization and
   supplies the tab with the sign-in URL.
9. In that tab, sign in through Snowflake or its identity provider using your
   normal browser/passkey and consent to the read role. The same Studio user
   must complete the callback. Return to the original Studio tab.
10. Studio continues the challenged question once consent completes. For the same
    Studio session it requests fresh 3LO again once its saved consent is an hour
    old. If Gateway asks for consent after a tool has already completed, Studio
    does not replay the question automatically; review it and run it explicitly.
    Verify a successful `CURRENT_USER()` or metadata tool result before treating
    the Snowflake connection as working.

Tool discovery requires no Snowflake sign-in. Data operations require the current
user's consent. Another Studio user must connect their own account. Validate
`CURRENT_USER()` for each person and test a known unauthorized object as well.
Read-role access, row policies and masking policies remain Snowflake's authority.

## Recovery, maintenance and rollback

- Preserve each state directory. Uncertain operations keep their original
  request/version IDs. Re-run the same command with the same inputs to reconcile.
  `oauth_setup.py configure` requires the same hidden client credentials during
  recovery; confirmed secret versions are not rewritten.
- Artifact uploads use immutable content-addressed keys. Larger ZIPs use bounded
  S3 parts and checksums. After a failed upload, first reconcile without a retry;
  only after a fresh HEAD confirms absence use `--retry-upload` with the same
  package. Confirmed parts are reused. A lost multipart-create acknowledgement
  requires inspecting S3's multipart inventory for that exact key; do not create
  another upload or erase the journal.
- For an explicit package update use `deploy.py deploy --new-release` with the
  same configuration/state and a newly built package. Prior versions are retained.
- To migrate an earlier WIF or Runtime-owned OAuth deployment, remove only
  `oauth`/`auth_source` from its config and add `"auth_source": "gateway"`.
  Run `deploy.py migrate-gateway` with that config and the existing state, then
  deploy with `--new-release`. Retire unused old-mode registrations first.
  The migration checks the exact live artifact and preserves previous receipts.
- The **Connected** label records consent completion. Only a successful tool call
  proves current Snowflake access. Gateway refreshes tokens or requests new
  consent when needed. Studio login expiry requires signing into Studio again.
- A failed or uncertain callback offers explicit status checking. Do not complete
  a callback under another user or replay it with modified state/session values.
- AgentCore authorization URLs and their session identifiers expire after
  ten minutes. If its callback reports a missing `cookie`, compare the callback
  time with the saved flow's expiry before diagnosing browser settings or
  credentials. The 2026-10-03 occurrence returned after its flow had expired.
  Reconcile that attempt, renew Studio sign-in if needed, and start a fresh
  authorization in the same browser profile.
- To disable access, revoke the Studio capability grant or the Snowflake user's
  read-role grant. To disable this client for everyone:
  `ALTER SECURITY INTEGRATION STUDIO_RUNTIME_MCP_OAUTH SET ENABLED = FALSE;`
- Roll back Runtime code only after reviewing the retained artifact and compatible
  environment/IAM template. Do not silently restore shared WIF authentication.
  Keep the previous release receipt and confirm the live version after rollback.
- Delete unused registrations/authentication through Studio before manually
  retiring the separate stacks. In-use connections are protected. Retained
  artifacts/logs/secrets require explicit cleanup; no automatic teardown runs.
- A second AWS account needs its own config, state, native OAuth provider,
  Snowflake integration and exact callback. Nothing connects it to another
  customer's Snowflake automatically.

## Consent continuation repair — 2026-10-02

The hosted v7 consent flow completed provider sign-in but left its saved
invocation in `AUTHORIZATION_REQUIRED`. Consent completion did not enqueue the
question, and the original Studio page stopped polling at that phase.

The local repair links a first-tool Gateway challenge to its original job and
idempotently enqueues the saved question when the same Studio user completes
consent. The agent page also observes pending consent independently of the
Chat tab, so an older saved flow can resume after sign-in without a button.
Questions are not replayed if a tool already completed or the completion
count is unknown.

Validation: the full backend suite (1,973 passed, 4 skipped), all 86 local
Chrome browser tests, the frontend build, Python compilation, and
`git diff --check` passed. The receipt-bound callback-resume release updated
only `backend/app.py`, `backend/journey.py`, and
`backend/mcp_user_connections.py` in the existing Business and Worker
Lambdas. The published frontend index and script hashes match CloudFront
readback. Pre- and post-release existing-resource security audits each
passed 69 checks. These checks do not prove a hosted Snowflake query; real
authenticated consent and table discovery remain unverified.

## Studio session authorization repair — 2026-10-02

The application previously forced provider authorization whenever an invocation
started a new conversation, even if the same Studio session had just completed
consent. Authorization is now recorded per signed-in Studio session and MCP
server. Messages and new conversations reuse that record until the one-hour
interval expires; a new Studio sign-in session requires its own consent.
Separate browser sessions retain independent records. The browser uses the same
server decision to decide whether to open the provider tab.

The existing Business and Worker Lambdas and frontend bundle were updated.
No agent revision or Runtime deployment was required. Backend tests passed
(1,976 passed, 4 skipped), all 86 local Chrome tests passed, and build,
compilation, and diff checks passed. Both security/tag audits passed 65 resource
checks. CloudFront returned the reviewed HTML and script digests after its
invalidation completed.

The native browser check identified the user's main Chrome instance, but the
Mac locked before authenticated acceptance could run. Live Snowflake consent,
query success, and the reported `verify_roles` failure remain unverified.
Evidence and rollback predecessors are retained under
`artifacts/runtime-snowflake-mcp-20260930/default-gateway/native-gateway-20261002/session-auth-20261002/`.

## Secondary-role guard repair — 2026-10-03

A failure at `session_setup/verify_roles` means Snowflake login and the
role-inspection query succeeded, the primary role matched, and the
secondary-role fields parsed as strings. That failure does not establish
invalid credentials or an inaccessible Snowflake account.

The guard now accepts either an empty requested-role string or `NONE` when
the activated secondary-role string is empty. It still requires the exact
configured primary role and rejects active roles, `ALL`, unknown values,
missing fields, and malformed data before running the requested statement.
The connector continues to request `secondary_roles="NONE"` during login.

The regression failed with the original `verify_roles` result before the
repair. All 86 Snowflake MCP tests and 32 affected Gateway/Runtime contract
tests passed. Only `snowflake_mcp/database.py` changed in the deployed
package. The existing isolated Snowflake MCP Runtime reached version 7,
its DEFAULT endpoint is READY, and CloudFormation reached UPDATE_COMPLETE.
Runtime security/tag audits passed all 27 checks before and after release;
the authenticated facade passed its 19 checks.

Hosted acceptance is partial. Native consent and automatic question
continuation were verified in the user's normal Chrome profile. Separate
`test_connection` and `list_databases` requests succeeded under the exact
reader role and warehouse, returning five accessible databases. Both have
matching immutable Runtime receipts and Snowflake query IDs. The role
guard therefore permitted real authenticated statements after release;
the exact live secondary-role string was not separately captured.

The broader resumed invocation failed with `RuntimeClientError`; its
cause remains unconfirmed. The schema/table request was then rejected as
Studio's session neared expiry. Studio sign-in requires user authentication
before those remaining checks can run, so table-discovery acceptance
remains open.

Earlier native callbacks returned HTTP 400 with
`Value at 'cookie' failed to satisfy constraint: Member must not be null`
after their ten-minute authorization windows had expired. Those failed
attempts remain recorded separately from the later successful consent.

Sanitized test, deployment, query, and failure evidence is retained under
`artifacts/runtime-snowflake-mcp-20260930/default-gateway/empty-secondary-roles-20261003/`.
Its `verification.md` distinguishes verified queries from the pending
schema/table checks.

## References

- [Snowflake current secondary roles](https://docs.snowflake.com/en/sql-reference/functions/current_secondary_roles)
- [Snowflake OAuth custom clients](https://docs.snowflake.com/en/user-guide/oauth-custom)
- [Snowflake OAuth integration SQL](https://docs.snowflake.com/en/sql-reference/sql/create-security-integration-oauth-snowflake)
- [AgentCore workload access tokens](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/get-workload-access-token.html)
- [AgentCore OAuth session binding](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/oauth2-authorization-url-session-binding.html)
- [Gateway MCP targets](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-MCPservers.html)
- [AgentCore Runtime header allowlist](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-header-allowlist.html)
