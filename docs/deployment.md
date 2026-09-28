# Deploy and update Agent Studio

The repository generates CloudFormation from `infra/` and packages a locked Linux
ARM64/Python 3.13 Lambda ZIP. The frontend is a Vite build uploaded to the private
web bucket behind CloudFront. Credentials and deployment state are not in Git.

## Choose your deployment target

A fresh installation creates the authenticated console, agent runtime infrastructure,
an empty IAM-authenticated AgentCore Gateway and an empty AWS Agent Registry. It
creates no MCP targets, provider credentials, Snowflake objects, or provider-specific
catalog entries. The initial templates contain no selected tools or example data.
Publish models and onboard your own MCP endpoints after signing in.

Set these values for **your** installation; account IDs below are placeholders:

```sh
GAB_ACCOUNT=YOUR_12_DIGIT_AWS_ACCOUNT_ID
GAB_PROFILE=YOUR_AWS_PROFILE
GAB_REGION=YOUR_AWS_REGION
GAB_STATE="artifacts/account-${GAB_ACCOUNT}-${GAB_REGION}/release-state.json"
GAB_TARGET=(--expected-account "$GAB_ACCOUNT" --profile "$GAB_PROFILE"
  --region "$GAB_REGION" --state "$GAB_STATE")
GAB_RELEASE="mcp-${GAB_ACCOUNT}-${GAB_REGION}-initial"
mkdir -p "artifacts/$GAB_RELEASE"
```

For an existing installation, reuse its protected state. It binds exact stack IDs,
outputs, release artifacts, Gateway, Registry and enabled `journeyPlatform`
configuration. Never copy another account's state to bypass a binding check.
Keep a protected backup of state and receipts; a clone does not contain live
installation identity. Fixed stack names support one installation per account/region.
Read the application URL from `app.outputs.ApplicationOrigin` in your state.

## Build and verify

From the repository root:

```sh
uv sync --locked --extra foundation-runtime
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python -m pytest -q
npm --prefix frontend run test:e2e -- mcp-onboarding.spec.ts mcp-servers.spec.ts
git diff --check
```

For an agent-runtime/UI change, also run the affected journey browser suite:

```sh
npm --prefix frontend run test:e2e -- --config playwright.journey.config.ts
```

Package the application:

```sh
.venv/bin/python -m scripts.serverless_package
```

If your existing installation has explicitly approved temporary QA enrollments,
retain its target-specific file with `--qa-enrollments <protected-file>`. Do not
reuse QA identities from another account.

The builder exports the frozen dependency lock, installs Linux ARM64 wheels and
copies only the required application/runtime files. It excludes docs, tests,
local environment files, deployment state and credentials. Record the printed
ZIP SHA-256 and verify it against every deployed application Lambda.

## Routine code deployment

These commands are for the existing approved application. Shell arrays below work
in bash and zsh:

```sh
# Reuse the target variables above and choose a new receipt name for this release.
GAB_RELEASE="mcp-${GAB_ACCOUNT}-${GAB_REGION}-YOUR_RELEASE_NAME"
mkdir -p "artifacts/$GAB_RELEASE"

.venv/bin/python -m scripts.serverless_deploy preflight "${GAB_TARGET[@]}"
.venv/bin/python -m scripts.mcp_onboarding_audit --existing \
  --state "$GAB_STATE" \
  --output "artifacts/$GAB_RELEASE/security-before.json"

.venv/bin/python -m scripts.serverless_deploy deploy "${GAB_TARGET[@]}"
.venv/bin/python -m scripts.serverless_deploy publish "${GAB_TARGET[@]}"
.venv/bin/python -m scripts.serverless_deploy status "${GAB_TARGET[@]}"

.venv/bin/python -m scripts.mcp_onboarding_audit --existing \
  --state "$GAB_STATE" \
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

This release only permits its named MCP policies, removal of the retired
`McpCreation` worker policy, and application Lambda code updates, plus unchanged
Lambda-ARN references in API integrations. It rejects
resource replacements and changes to the pool, endpoints, unrelated IAM or data
resources. It measures actual total inline IAM policy size before execution,
validates required `auto-delete=no` tags, and records write intents before native
operations. Keep the same evidence directory when reconciling an interrupted
release. Do not erase receipts or repeat uncertain writes with a new request ID.

The `registry`/`configure` actions and `mcp_credentials_deploy` stages are initial
configuration operations, not routine redeployment. Existing targets already have
these settings; rerunning bootstrap can conflict with saved configuration.

## First installation

Use an initially absent state path for the target you selected above. Build,
test and package first. Then run the following commands from the repository root:

```sh
.venv/bin/python -m scripts.serverless_deploy preflight "${GAB_TARGET[@]}"
.venv/bin/python -m scripts.serverless_deploy artifacts "${GAB_TARGET[@]}"
.venv/bin/python -m scripts.serverless_deploy deploy "${GAB_TARGET[@]}"

# Runtime artifact, private evidence storage, scoped roles, empty Gateway,
# and generic instruction templates. No model or external service is invoked.
.venv/bin/python -m scripts.journey_platform prepare "${GAB_TARGET[@]}"

# New AWS Agent Registry and an empty onboarding configuration.
.venv/bin/python -m scripts.mcp_onboarding_deploy registry \
  --evidence-name "$GAB_RELEASE" "${GAB_TARGET[@]}"
.venv/bin/python -m scripts.mcp_onboarding_deploy configure \
  --evidence-name "$GAB_RELEASE" "${GAB_TARGET[@]}"

# Permit Gateway to use only credentials subsequently created in this deployment.
# This grants a namespace; it creates no secret or provider.
.venv/bin/python -m scripts.mcp_credentials_deploy gateway \
  --evidence-name "$GAB_RELEASE" "${GAB_TARGET[@]}"

# Initial activation includes model administration, generic MCP onboarding and
# credential setup permissions. Routine updates use serverless_deploy instead.
.venv/bin/python -m scripts.journey_platform activate "${GAB_TARGET[@]}"
.venv/bin/python -m scripts.serverless_deploy publish "${GAB_TARGET[@]}"
.venv/bin/python -m scripts.serverless_deploy status "${GAB_TARGET[@]}"
.venv/bin/python -m scripts.mcp_onboarding_audit --existing \
  --state "$GAB_STATE" --output "artifacts/$GAB_RELEASE/security-after.json"
```

Retain the same state and evidence name when reconciling an interrupted stage.
Native writes retain request intents; uncertain acceptance requires GET-based
reconciliation. Never delete a receipt to retry. `prepare` preserves an already
configured platform and does not republish its catalog or connections. Routine
code deployment does not run provider bootstrap scripts.

Invite your administrator through the new Cognito pool and assign `studio-admin`.
Assign business users their approved `studio-research` or `studio-operations`
group. Complete sign-in and email verification through the deployed application.
In model administration, discover, test and publish a Bedrock model available in
your account/region before deploying an agent. No third-party MCP account is
required to install or use the platform.

Open **MCP servers** to save your own authentication, register an endpoint, review
its tools and publish it through Registry. Follow the [generic onboarding guide](generic-mcp-onboarding.md).
To connect Snowflake, deliberately follow the separate [Snowflake SQL/PAT guide](snowflake-managed-mcp.md)
using your own Snowflake account. Platform installation does not run that guide.

Snowflake objects and PATs are managed manually in Snowflake. The platform has no
Snowflake provisioning script, creation profile or preselected Snowflake catalog.
Previously registered connections remain visible and protected by saved-agent
dependencies; removing the old provisioning code does not delete their remote
objects, credentials or saved agent versions. Unfinished legacy provisioning jobs
stop with an unknown outcome and retain their native receipts for operator
inspection. New connections use the generic onboarding flow above.

For an existing installation that still has `McpCreation` permissions, deploy this
cleanup through the reviewed **MCP permission changes** procedure. Routine
code-only preflight intentionally rejects that infrastructure difference. The
permission release removes the creator policy while preserving existing
Cognito groups and generic credential/Gateway access. Keep the saved deployment
state, including legacy metadata, until an explicit state migration is reviewed.

## Authenticated acceptance

1. Open the deployed CloudFront URL and sign in through Cognito. Confirm the
   expected workspace and that anonymous `/api/me` is rejected.
2. Open **MCP servers**. On a fresh installation, confirm zero saved credentials
   and registrations and that **Create MCP connection** and **Add authentication
   connection** are available. Registry and Gateway must contain no MCP records or
   targets until an administrator onboards one. On an existing installation,
   verify its registrations and protected dependencies are preserved.
3. Publish an available model and run a prompt-only agent. After deliberately
   onboarding your own endpoint, verify an actual selected tool call through
   Gateway, its Registry record and conversation persistence after reload.
   Installing the platform alone does not establish connectivity to any provider.
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
