# Deploy and update Agent Studio

For a new installation, supply one configuration file and run the installer.
It builds the application, provisions AWS resources, configures the platform,
publishes the frontend and runs the infrastructure audits.

This guide ends with verification of the deployed app and its access controls.
After installation, use the [user guide](start-here.md) to publish models,
configure MCP connections and create agents.

## Prerequisites

Run commands from the root of the release checkout you intend to install.

| Requirement | What you need |
| --- | --- |
| Workstation or deployment runner | Python 3.13, `uv`, Node.js 22.12+, npm and AWS CLI v2. Builders download locked Linux ARM64 wheels. Docker and a CDK bootstrap stack are not required. |
| AWS target | An account and commercial AWS region supporting Bedrock models, AgentCore Runtime, Gateway, Identity, Evaluations and AWS Agent Registry. Fixed stack names support one Studio installation per account and region. |
| AWS profile | An authenticated deployment profile for that account. For IAM Identity Center, run `aws sso login --profile YOUR_PROFILE` before deployment. The installer verifies the caller's account before writing to AWS. |
| Deployment permissions | CloudFormation, scoped IAM/PassRole and service-linked roles, S3, CloudFront, Cognito, API Gateway, Lambda, DynamoDB, SQS, CloudWatch/logs, AgentCore, Registry and Secrets Manager. Include resource tagging and readback permissions used by the templates and audits. Have your AWS administrator review the templates in `infra/`. |
| Quotas | Capacity for the generated Lambda functions and AgentCore resources. Worker memory and optional hosting concurrency are configurable below. Use the target account's quotas when choosing values. |

Deployment creates billable resources. Review the templates and preserve the
generated state and receipts. Persistent data and release artifacts use retained,
encrypted resources; application access requires authentication.

No model subscription, MCP server or external provider account is needed to
install Studio. Those are configured when you begin using the application.

## Choose your deployment target

Install dependencies and copy the configuration:

```sh
uv sync --python 3.13 --locked --extra foundation-runtime
mkdir -p artifacts
cp deploy/studio.example.json artifacts/studio-install.json
```

Open `artifacts/studio-install.json` in your editor. Replace the example account,
profile and region with your target. To find the account ID, replace
`YOUR_PROFILE` with your authenticated profile's name and run:

```sh
aws sts get-caller-identity --profile YOUR_PROFILE --query Account --output text
```

Copy that ID as a quoted JSON string. Choose the deployment region explicitly;
the profile does not need a default region.

```json
{
  "account": "123456789012",
  "profile": "your-deployment-profile",
  "region": "us-west-2",
  "worker_memory_size": 512,
  "package_reserved_concurrency": null,
  "user_oauth": true,
  "package_uploads": true
}
```

| Field | Meaning |
| --- | --- |
| `account` | Your 12-digit AWS account ID, as a JSON string. The example number is a placeholder. |
| `profile` | Your authenticated AWS SDK/CLI profile name. There is no required profile name. |
| `region` | The AWS region where this installation will run. `us-west-2` is an example, not a required deployment region. |
| `worker_memory_size` | Lambda worker memory in MiB; default `512`, allowed range `512`–`10240`. New accounts can initially be restricted to 512 MiB. Increase this only after confirming the target account permits the higher value. |
| `package_reserved_concurrency` | Optional reservation for each package-hosting Lambda. `null` uses the account's shared concurrency; a positive integer requests that reservation. |
| `user_oauth` | `true` installs the Gateway needed for per-user OAuth 3LO. Hosted IAM, PAT/API-key and service OAuth 2LO connections can use `false`. |
| `package_uploads` | `true` installs complete Python MCP ZIP hosting. Set `false` if administrators will only connect existing hosted endpoints. |

The file contains deployment choices only. Enter user passwords and provider
secrets in Cognito or the authenticated Studio setup form when needed.

The installer derives state and evidence paths:

- `artifacts/account-<account>-<region>/release-state.json`
- `artifacts/install-<account>-<region>/`

These paths are excluded from Git. Keep protected backups: they bind stack IDs,
configuration, artifact versions and submitted operations to this installation.
An optional `evidence_name` configuration field can name the evidence directory;
keep it unchanged when resuming.

## First installation

Review the selected stages:

```sh
.venv/bin/python -m scripts.studio_install plan --config artifacts/studio-install.json
```

`plan` reads local configuration and makes no AWS calls. Confirm the account,
profile, region and enabled features in its output. Then deploy:

```sh
.venv/bin/python -m scripts.studio_install deploy --config artifacts/studio-install.json
```

The command runs the following stages in order:

| Stage | Work performed by the installer |
| --- | --- |
| Preflight and build | Verify target ownership, check templates and IAM policy sizes, install locked frontend dependencies, build the frontend and package Linux ARM64/Python 3.13 code. |
| Base application | Create private artifact storage, upload the verified application ZIP and create CloudFront, Cognito, authenticated API routes, Lambda, DynamoDB and SQS. |
| Runtime and catalog | Install missing AWS-managed Runtime prerequisites, create the agent runtime infrastructure and empty IAM Gateway, and publish generic skills/templates. |
| Registry and credentials | Create an empty AWS Agent Registry and configure the platform's credential permissions. |
| User OAuth, if enabled | Install and configure the Cognito-authenticated Gateway used for per-user MCP authorization. The installer generates its artifact receipt and Studio return URL. |
| Package hosting, if enabled | Build the locked hosting bundle, upload its immutable version, derive the bucket/table/worker settings and install the authenticated hosting service and permissions. |
| Activation and publication | Apply the complete platform settings, verify deployed Lambda code, publish frontend assets and wait for CloudFront invalidation. |
| Audit | Verify resource readiness, private access, encryption, scoped permissions and the selected integrations. |

There are no inline Python snippets, manual DynamoDB updates, artifact-receipt
construction or manual tagging commands to execute. Resource metadata used by
the repository's ownership checks is managed by the installers.

On success, every selected stage is `COMPLETE`. The output includes
`ApplicationOrigin` and `UserPoolId`. A fresh installation has no MCP registrations,
provider credentials or published models. Configure those in Studio after user
enrollment.

`user_acceptance` remains `NOT_RUN`: this field tracks the separate business-user
and tool journey, which the installer does not run. It does not indicate an
installation failure.

## Verify the installation

1. Run the `status` command below. Every selected installation stage must be
   `COMPLETE`; omitted optional features have no stage.
2. Run the `audit` command below. It checks the actual deployed resources against
   the saved installation: native readiness, private storage, authenticated APIs,
   encryption, permissions and enabled hosting features.
3. Open the printed `ApplicationOrigin` in a browser. Confirm that the Studio
   page and its assets load and that **Sign in** opens the installation's Cognito
   sign-in page. Check the browser's Network and Console panels for failed asset
   requests or application errors.
4. In a signed-out browser session, open `<ApplicationOrigin>/api/me`. The
   protected API must reject the request with HTTP `401` or `403`.
5. Keep the status output, audit reports and sanitized browser observations with
   the installation evidence. Exclude credentials, cookies and authorization
   headers.

These checks verify deployment and initial access. They do not require creating
test users, invoking a model, uploading an MCP package or connecting to Snowflake.
To enroll the app's first users, follow [Create administrator and business users](#create-administrator-and-business-users).

## Status and interruption recovery

Inspect saved progress:

```sh
.venv/bin/python -m scripts.studio_install status --config artifacts/studio-install.json
```

`status` reads local receipts. To verify the installed resources against AWS:

```sh
.venv/bin/python -m scripts.studio_install audit --config artifacts/studio-install.json
```

If deployment stops, preserve its configuration, source checkout, generated
packages and receipts. Run the same `deploy` command to resume. Completed stages
are skipped after input verification; native-operation receipts reconcile a
submitted write before the pipeline continues. An unresolved submission stops
the installer for investigation instead of issuing another create request.

CloudFormation status and template reads tolerate up to three transport attempts.
Writes are submitted once and reconciled from their receipts. If a network or
read timeout still stops the installer, restore connectivity and rerun that same command.
For example, a timed-out `DescribeStacks` check does not mean the stack creation
failed. The installer checks the saved operation and AWS state before continuing;
keep the completed build, uploaded artifacts and receipts. If it still reports
an unresolved submission, inspect the named native operation rather than
deleting state or starting a second installation.

Do not delete state, switch account/profile, rebuild different source or change
capacity settings to bypass an interrupted operation. Inspect the saved stage,
the corresponding CloudFormation events and the native resource status. If AWS
rejected a request, resolve the cause and reconcile its receipt before attempting
a reviewed replacement operation. The installer refuses unrecorded existing apps.

### Apply an installer fix during a partial installation

If a maintainer supplies a reviewed fix to the deployment scripts, accept it
before resuming:

```sh
.venv/bin/python -m scripts.studio_install_repair --config artifacts/studio-install.json
.venv/bin/python -m scripts.studio_install deploy --config artifacts/studio-install.json
```

The repair command verifies the original source manifest, unchanged application
source and built packages, and the target's native ownership. It records the
installer change without deploying, rebuilding or resubmitting cloud operations.
Only Python files under `scripts/` may change. Changes to application code,
infrastructure templates, dependencies or deployment configuration are rejected.
Keep the generated `source-manifest.json` with the other installation receipts.

Older receipts may lack that file. In that case, a maintainer must supply the
original manifest and add `--previous-source-manifest PATH` to the repair command.
Its digest must match the source recorded when installation began; a manifest
generated from changed source will be rejected.

### Recover a rejected worker memory setting

If AWS rejects `Worker` with a `MemorySize` limit, wait for the application stack
to reach `UPDATE_ROLLBACK_COMPLETE`. For example, when AWS reports a maximum of
512 MiB:

```sh
.venv/bin/python -m scripts.studio_install_repair --config artifacts/studio-install.json --worker-memory-size 512
.venv/bin/python -m scripts.studio_install deploy --config artifacts/studio-install.json
```

Use a value within the limit shown by AWS. If you received updated installer
scripts, accept that reviewed fix with the repair command in the preceding
section first.

Recovery verifies that this installation's failed update restored its original
application template, parameters and resource identities. It updates only the
worker memory setting in the configuration file and protected platform settings.
The next deployment submits a new recorded activation attempt and retains the
failed attempt's receipt. Existing packages, stacks, users and stored data remain.
It refuses an unrelated rollback or a change to other platform settings.

## Create administrator and business users

1. In the AWS console, select your deployment account and region. Open
   **Cognito → User pools** and select the `UserPoolId` printed by the installer.
2. Under **Users**, create your administrator with a reachable email address
   and send an invitation. Add the user to `studio-admin` under **Groups**.
3. Create a business user and assign exactly one workspace group:
   `studio-research` or `studio-operations`.
4. To use both views with one person, assign that person `studio-admin`, exactly
   one business workspace group and `studio-role-switcher`. Multiple approved
   roles require the role-switcher group.
5. Open `ApplicationOrigin`. Complete Cognito sign-in, change the temporary
   password if prompted and verify the email address. Select **Platform Admin**
   in the role menu if the account has both roles.

Enter credentials directly into Cognito. Deployment does not create QA accounts
or require an enrollment file.

After enrollment, follow the [user guide](start-here.md) for model publication,
MCP onboarding and agent testing. The [Snowflake guide](snowflake-setup.md) covers
that optional provider's setup.

## Build and verify

Maintainers changing the release run the affected suites before deployment:

```sh
uv sync --python 3.13 --locked --extra foundation-runtime
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python -m pytest -q
(cd frontend && npx playwright install chromium)
npm --prefix frontend run test:e2e
npm --prefix frontend run test:e2e -- --config playwright.journey.config.ts
git diff --check
```

The installer handles packaging during first installation. For a reviewed
update using the existing release scripts, build its application package with:

```sh
.venv/bin/python -m scripts.serverless_package
```

Local tests use fixtures. They do not replace authenticated portal acceptance.

## Routine code deployment

`studio_install deploy` installs or resumes its pinned first release. Use the
existing release scripts for an update to an installed application.

Recover that installation's protected state and choose a new release evidence
directory. Preserve the current template/parameters, live Lambda hashes, package
versions and frontend index version before mutation. Set the target explicitly:

```sh
GAB_ACCOUNT=YOUR_12_DIGIT_ACCOUNT_ID
GAB_PROFILE=YOUR_AWS_PROFILE
GAB_REGION=YOUR_AWS_REGION
GAB_STATE=YOUR_EXISTING_RELEASE_STATE_PATH
GAB_RELEASE=YOUR_LOWERCASE_RELEASE_NAME
GAB_TARGET=(--expected-account "$GAB_ACCOUNT" --profile "$GAB_PROFILE"
  --region "$GAB_REGION" --state "$GAB_STATE")
mkdir -p "artifacts/$GAB_RELEASE"
```

After building and verifying the release, use the deployment sequence approved
for its scope. For a code-only update with an unchanged infrastructure template:

```sh
.venv/bin/python -m scripts.mcp_onboarding_audit --existing \
  --state "$GAB_STATE" --output "artifacts/$GAB_RELEASE/security-before.json"
.venv/bin/python -m scripts.serverless_deploy preflight "${GAB_TARGET[@]}"
.venv/bin/python -m scripts.serverless_deploy deploy "${GAB_TARGET[@]}"
.venv/bin/python -m scripts.serverless_deploy publish "${GAB_TARGET[@]}"
.venv/bin/python -m scripts.mcp_onboarding_audit --existing \
  --state "$GAB_STATE" --output "artifacts/$GAB_RELEASE/security-after.json"
```

Preflight rejects unreviewed template changes. Installations created before the
provider-neutral rendering tagged the Cognito user pool with a personal `owner`
entry that current renderings no longer emit. The deployment and scoped release
scripts automatically retain exactly that one tag value, read from the already
identity-verified live stack template, in both the preflight comparison and the
submitted update, so the live tag is never stripped or changed. Do not remove
the tag manually and do not edit live resources to make a preflight pass: the
tag is compatibility-only metadata, fresh installations never gain an `owner`
tag, and every other template difference is still rejected. The legacy
code-only preflight
uses CloudFormation's inline template limit; a fully enabled template can exceed
51,200 bytes. The first-installation pipeline and scoped MCP release support
versioned template uploads. Resolve a legacy preflight failure through a reviewed
release path that supports the intended change.

Updating platform Lambda code does not replace saved agents' pinned Runtime
packages. Foundation publication and existing-agent changes have their own
version and approval boundaries.

## MCP permission changes

For reviewed changes within the MCP release script's allowed scope:

```sh
.venv/bin/python -m scripts.mcp_onboarding_deploy deploy \
  --evidence-name "$GAB_RELEASE" "${GAB_TARGET[@]}"
.venv/bin/python -m scripts.mcp_onboarding_deploy publish \
  --evidence-name "$GAB_RELEASE" "${GAB_TARGET[@]}"
```

Run the audits before and after, then repeat affected portal acceptance. The
script allows named MCP policy changes and application code updates, and rejects
unrelated replacements or identity/data changes.

Adding an optional feature to an existing installation is an infrastructure
upgrade. Review the corresponding installer in
`scripts/mcp_oauth_gateway_deploy.py` or
`examples/runtime-snowflake-mcp/facade_deploy.py` and its application permission
change together. The first-installation command refuses to adopt that existing
application.

## Troubleshoot installation

| Symptom | Next action |
| --- | --- |
| AWS profile expired | Renew that profile's session, then resume with the same configuration. |
| Network error or AWS read timeout | Restore connectivity, then rerun the same `deploy` command. Saved receipts reconcile submitted operations; a failed status read alone is not evidence of a failed deployment. |
| Account or stack/state mismatch | Recover the correct target state and credentials. Do not substitute another account's file. |
| Configuration, source or package digest changed | Restore the recorded inputs. A reviewed deployment-script fix can use the installer repair command above; application or infrastructure changes require a separately reviewed upgrade. |
| `NEEDS_RECONCILIATION` | Inspect that stage's receipt and native status, then rerun the same deployment command. If acceptance is still unknown, investigate the saved request before another submission. |
| Worker `MemorySize` rejected | Wait for `UPDATE_ROLLBACK_COMPLETE`, then use the worker-memory recovery above with a value within the native limit. New installations default to 512 MiB. |
| Lambda concurrency quota rejection | Check the target account's quota and the configuration chosen before installation. Reconcile a submitted stack before changing its intent. |
| 3LO or package upload is unavailable | Check the corresponding option and stage in `status`. Both infrastructure configuration and application activation must complete. |
| Audit fails | Inspect the named resource/check in the evidence directory. Leave the installation unaccepted until the underlying check passes. |

## Rollback and recovery

Retain the preceding application template and content-addressed package before
an update. For a code rollback, restore the verified artifact through a reviewed
CloudFormation update and verify the resulting Lambda hashes. Restore the saved
frontend index version and its referenced assets, invalidate CloudFront and wait
for completion. Update local release state only after native readback.

Preserve DynamoDB data, agents, Gateway targets, Registry records and credentials.
A code rollback does not authorize their deletion. Inspect any failed or
in-progress operation before another mutation; connection-level recovery is
described in the [MCP guide](generic-mcp-onboarding.md).
