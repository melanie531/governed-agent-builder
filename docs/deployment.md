# Deploy and update Agent Studio

The repository generates CloudFormation from `infra/` and packages a locked Linux
ARM64/Python 3.13 Lambda ZIP. The frontend is a Vite build uploaded to the private
web bucket behind CloudFront. Credentials and deployment state are not in Git.

## Existing application

| Binding | Current value |
| --- | --- |
| Application | https://d11jko88tox2zk.cloudfront.net |
| AWS account / region | `250708454815` / `us-east-1` |
| Explicit SDK profile | `default` |
| Application stack | `governed-agent-builder-serverless-app` |
| Agent integration stack | `governed-agent-builder-journey` |
| Target state | `artifacts/account-250708454815-us-east-1/release-state.json` |

Reuse that state for this application. It binds exact stack IDs, outputs, release
artifacts, Gateway, Registry and the enabled `journeyPlatform` configuration.
Do not create a second application or copy another account's state to bypass a
binding check. Keep a protected backup of the state and release receipts; a clone
alone does not contain live deployment identity.

## Build and verify

From the repository root:

```sh
uv sync --locked --extra foundation-runtime
npm --prefix frontend ci
.venv/bin/python -m pytest -q
npm --prefix frontend run build
npm --prefix frontend run test:e2e -- mcp-onboarding.spec.ts mcp-servers.spec.ts
git diff --check
```

For an agent-runtime/UI change, also run the affected journey browser suite:

```sh
npm --prefix frontend run test:e2e -- --config playwright.journey.config.ts
```

Package the application. On the existing QA-enabled deployment, retain its
operator-approved enrollment file so updating code does not remove its test
access. A normal deployment without temporary QA enrollments omits this option.

```sh
.venv/bin/python -m scripts.serverless_package \
  --qa-enrollments artifacts/account-250708454815-us-east-1/journey-qa-enrollments.json
```

The builder exports the frozen dependency lock, installs Linux ARM64 wheels and
copies only the required application/runtime files. It excludes docs, tests,
local environment files, deployment state and credentials. Record the printed
ZIP SHA-256 and verify it against every deployed application Lambda.

## Routine code deployment

These commands are for the existing approved application. Shell arrays below work
in bash and zsh:

```sh
GAB_TARGET=(--expected-account 250708454815 --profile default --region us-east-1
  --state artifacts/account-250708454815-us-east-1/release-state.json)
GAB_RELEASE=repository-cleanup-20260926
mkdir -p "artifacts/$GAB_RELEASE"

.venv/bin/python -m scripts.serverless_deploy preflight "${GAB_TARGET[@]}"
.venv/bin/python -m scripts.mcp_onboarding_audit --existing \
  --state artifacts/account-250708454815-us-east-1/release-state.json \
  --output "artifacts/$GAB_RELEASE/security-before.json"

.venv/bin/python -m scripts.serverless_deploy deploy "${GAB_TARGET[@]}"
.venv/bin/python -m scripts.serverless_deploy publish "${GAB_TARGET[@]}"
.venv/bin/python -m scripts.serverless_deploy status "${GAB_TARGET[@]}"

.venv/bin/python -m scripts.mcp_onboarding_audit --existing \
  --state artifacts/account-250708454815-us-east-1/release-state.json \
  --output "artifacts/$GAB_RELEASE/security-after.json"
```

Use a new evidence directory for each distinct release. Before mutation, preserve
the current target state, CloudFormation template and parameters, deployed Lambda
hashes, and S3 version ID of `index.html` in that directory. Never include secrets,
cookies, tokens or raw customer data in evidence.

`preflight` validates the explicit STS identity, stack ownership and matching live
outputs. It synthesizes the application **with the saved journey/MCP settings**.
For an existing app it requires the complete template to match the deployed one;
it rejects incomplete state and unreviewed infrastructure changes before upload.
A routine release updates only the packaged code parameter. CloudFormation must
finish successfully before the release hash is updated. Publication uploads
assets before `index.html`, then waits for the CloudFront invalidation to complete.
No empty frontend build can be published.

This command deploys the platform's Lambda code. Existing agent versions retain
their pinned Runtime ZIP and manifest. Updating the Foundation used for future
agents is a separate artifact/publication change; do not silently replace saved
agent bindings or run initial catalog provisioning on every application release.

## MCP permission changes

For a scoped change to MCP worker/credential/Registry permissions, use the reviewed
change-set release rather than weakening routine preflight:

```sh
.venv/bin/python -m scripts.mcp_onboarding_deploy deploy \
  --evidence-name "$GAB_RELEASE" "${GAB_TARGET[@]}"
.venv/bin/python -m scripts.mcp_onboarding_deploy publish \
  --evidence-name "$GAB_RELEASE" "${GAB_TARGET[@]}"
```

This release only permits its named MCP policies and application Lambda code
updates, plus unchanged Lambda-ARN references in API integrations. It rejects
resource replacements and changes to the pool, endpoints, unrelated IAM or data
resources. It measures actual total inline IAM policy size before execution,
validates required `auto-delete=no` tags, and records write intents before native
operations. Keep the same evidence directory when reconciling an interrupted
release. Do not erase receipts or repeat uncertain writes with a new request ID.

The `registry`/`configure` actions and `mcp_credentials_deploy` stages are initial
configuration operations, not routine redeployment. Existing targets already have
these settings; rerunning bootstrap can conflict with saved configuration.

## First installation in a different account

This is a separate deployment requiring that account's approved profile/region.
Use a new, initially absent target-state path and substitute all four target
arguments. Fixed stack names permit one canonical installation per account/region.
Never pass the current account's state to a new target.

1. Build/package as above, omitting temporary QA enrollments unless deliberately
   configured for the new target.
2. Run `scripts.serverless_deploy preflight`, `artifacts`, `deploy`, and `publish`
   with the new target arguments. This creates the authenticated base console.
   The base template alone does not enable the live AgentCore journey.
3. For the Snowflake-backed starter, create the Snowflake objects and role-scoped
   PAT using [the Snowflake runbook](snowflake-managed-mcp.md). Provision its private
   Secrets Manager container with `infra.snowflake.credential_template()` through
   CloudFormation, carrying `auto-delete=no`. Set its value through the masked
   Secrets Manager editor to a JSON object with key `pat`. Do not put a PAT in
   CloudFormation parameters, source or shell arguments.
4. Prepare a **nonsecret** JSON file containing only the following fields, using a
   model/inference profile available in that target's Bedrock region:

   ```json
   {
     "endpoint": "https://ACCOUNT.snowflakecomputing.com/api/v2/databases/DATABASE/schemas/SCHEMA/mcp-servers/SALES_MCP",
     "secret_arn": "arn:aws:secretsmanager:REGION:ACCOUNT_ID:secret:governed-agent-builder-serverless/snowflake-pat-SUFFIX",
     "model": {
       "id": "bedrock-model",
       "name": "Approved model",
       "model_id": "AVAILABLE_BEDROCK_MODEL_OR_INFERENCE_PROFILE_ID",
       "provider": "MODEL_PROVIDER",
       "supports_temperature": true
     }
   }
   ```

5. Run `scripts.snowflake_platform validate`, then `prepare`, with
   `--config <nonsecret-json-path>` and the new target arguments. Preparation
   creates the versioned Runtime artifact, scoped integration stack, EXTERNAL PAT
   provider and IAM-authenticated Gateway, discovers Snowflake tools, probes the
   selected Bedrock model, and publishes the starter catalog. It records native
   write intents and stops for explicit reconciliation on uncertainty.
6. Run `scripts.mcp_onboarding_deploy registry`, then `configure`, to create the
   **new AWS Agent Registry** and bind generic onboarding to the existing Gateway.
   These commands use the starter's saved reader provider. Now run
   `scripts.journey_platform activate` with the same target arguments to enable
   the saved journey and onboarding configuration together, including its Cognito
   role-switcher group. This is an initial activation, not the routine update
   command for an existing platform.
7. Run
   `scripts.mcp_credentials_deploy prepare`, `scripts.mcp_onboarding_deploy deploy
   --evidence-name mcp-generic-ui`, `scripts.mcp_credentials_deploy gateway`,
   `scripts.mcp_credentials_deploy enable`, and finally
   `scripts.mcp_onboarding_deploy publish --evidence-name mcp-generic-ui`.
   Pass the same four target arguments to every command. The permission deployment
   must precede enabling credential creation. These stages reuse the app/Gateway;
   they do not create a preview application.
8. Invite the approved administrator/business users through the new Cognito pool,
   assign the appropriate Studio groups, and complete sign-in/email verification
   in the browser. Run the authenticated acceptance checks below.

The provider-specific bootstrap exists to reproduce the original starter catalog.
Once the platform is configured, onboarding additional providers uses the generic
UI and does not require Snowflake profiles. There is no one-command cross-account
migration or automatic adoption of existing stacks. A lost state file or failed
initial stack must be reconciled against retained receipts and actual resources.

## Authenticated acceptance

1. Open the deployed CloudFront URL and sign in through Cognito. Confirm the
   expected workspace and that anonymous `/api/me` is rejected.
2. Open **MCP servers**. Confirm the generic connection form, saved credentials,
   existing registrations and protected dependencies. Open the correct AWS Agent
   Registry console/account/region and inspect the published MCP record.
3. Open a working agent and run a bounded query such as `SELECT 1 AS
   MCP_CONNECTION_OK`. Verify the actual Gateway tool trace and reload to confirm
   conversation persistence. For runtime changes also test a new version/deployment
   and its selected tools; for evaluation changes verify native evaluation evidence.
4. Inspect browser requests, console/page errors and failed jobs. Wait for all
   touched CloudFormation, Lambda, Runtime and CloudFront operations to succeed.
5. Run the security/tag audit and retain sanitized evidence. Current audit tooling
   uses the bound QA parameter references when those identities are enrolled;
   their presence is not production user authentication or a model acceptance test.

## Rollback and recovery

Preserve the old application template/parameters and content-addressed ZIP before
release. For a code-only rollback, use the same verified template and set
`ArtifactKey` to the prior retained release through a reviewed UPDATE change set;
reject removals/replacements, then confirm every Lambda's SHA-256. Restore the
recorded S3 version of `index.html`, retain its referenced assets, invalidate
CloudFront, and wait for completion. Restore the recorded release hash in target
state only after readback verifies it. Never reset DynamoDB or remove agent data,
Gateway targets, Registry records or credentials as part of a code rollback.

If stack status is failed, rolling back or in progress, inspect its operation and
retained receipt before any next mutation. A pending acknowledgement is not
success. Connection-level recovery is documented in the [MCP guide](generic-mcp-onboarding.md).

The old preview deployer and one-time `verification-deploy` migration have been
removed. Email-verification resources are part of the normal application template.
Generated templates retain private S3, Cognito Code+PKCE, authenticated APIs,
scoped IAM, encryption and `auto-delete=no`; no Lambda Function URLs are used.
