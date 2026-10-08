# Agent Studio

A governed agent builder with a React/Cloudscape console, a FastAPI backend, and
AWS deployment on CloudFront, Cognito, API Gateway, Lambda, DynamoDB and SQS.
Business agents run on Amazon Bedrock AgentCore Runtime and call selected MCP
tools through AgentCore Gateway. Administrators onboard remote MCP endpoints,
review discovered tools, and publish them through AWS Agent Registry.

## Start here

- **Installing in a new AWS account:** follow [Deploy into a new AWS account](#deploy-into-a-new-aws-account) below. The [deployment guide](docs/deployment.md) explains the same commands, configuration and recovery.
- [User guide: configure an MCP connection, create an agent and test its tools](docs/start-here.md)
- [Update or recover an existing installation](docs/deployment.md#routine-code-deployment)
- [Snowflake setup: managed MCP with PAT or 3LO, Python MCP upload, and agent testing](docs/snowflake-setup.md)
- [Onboard and manage any supported MCP endpoint](docs/generic-mcp-onboarding.md)
- [Agent creation, runtime, conversation and deletion contracts](docs/create-agent-journey.md)
- [Serverless architecture and identity boundaries](docs/SERVERLESS-HOSTING.md)

A new installation starts with an empty AgentCore Gateway and AWS Agent Registry.
It creates no remote MCP connection or provider credential. Administrators publish
models and configure their own endpoints and authentication in Studio. Snowflake
is an optional manual integration, not part of platform deployment.

## Deploy into a new AWS account

Start from this repository's root directory. You need Python 3.13, `uv`, Node.js
22.12+, npm, AWS CLI v2, and an authenticated AWS deployment profile. Select an
account and region where Bedrock, AgentCore Runtime/Gateway and AWS Agent Registry
are available. Your operator needs permission to create the resources and IAM
roles described in the [prerequisites](docs/deployment.md#prerequisites).

Install the locked dependencies and copy the deployment configuration:

```sh
uv sync --python 3.13 --locked --extra foundation-runtime
mkdir -p artifacts
cp deploy/studio.example.json artifacts/studio-install.json
```

Find the account ID for your deployment profile; replace `YOUR_PROFILE` with its
name:

```sh
aws sts get-caller-identity --profile YOUR_PROFILE --query Account --output text
```

Edit `account`, `profile` and `region` in `artifacts/studio-install.json` for your
AWS target, keeping the account ID in quotes. The example enables per-user OAuth
and complete Python MCP ZIP uploads. The [configuration table](docs/deployment.md#choose-your-deployment-target)
explains the optional features and capacity settings. The worker starts at
512 MiB to support restricted new accounts; increase it only when your AWS
account permits the higher value.

```sh
.venv/bin/python -m scripts.studio_install plan --config artifacts/studio-install.json
.venv/bin/python -m scripts.studio_install deploy --config artifacts/studio-install.json
```

The deployment command builds the application, uploads its packages, installs
the selected infrastructure, configures the platform, publishes the frontend
and runs the security checks. It generates the artifact receipts and hosting
settings from your deployment. No inline Python, database edits or manual tag
commands are part of installation.

Check the result:

```sh
.venv/bin/python -m scripts.studio_install status --config artifacts/studio-install.json
.venv/bin/python -m scripts.studio_install audit --config artifacts/studio-install.json
```

Every selected stage should be `COMPLETE`, and the live audit should pass. Open
the printed `ApplicationOrigin` and confirm that Studio offers sign-in. The
[installation checks](docs/deployment.md#verify-the-installation) cover the
published app and its protected API.

To start using Studio, [create your administrator](docs/deployment.md#create-administrator-and-business-users).
Model publishing, MCP connections, provider consent and agent testing are separate
configuration tasks covered by the [user guide](docs/start-here.md). They are not
required to deploy the app, and installation does not require a Snowflake account.

Keep the generated state and receipts in protected storage. The installer resumes
with the same command and verifies its saved inputs before continuing. It supports
one installation per AWS account and region and refuses to adopt an existing app.
Use the [deployment guide](docs/deployment.md) for interruption recovery, installation
evidence and updates to an installed application.

## Local development

Use Python 3.13, `uv`, Node.js 22.12+ and npm. Dependency versions are pinned in
`uv.lock` and `frontend/package-lock.json`.

```sh
uv sync --locked --extra foundation-runtime
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python -m pytest -q
```

`./scripts/start.sh` builds and starts the local development application on
`http://127.0.0.1:5187`. Local mode uses SQLite, synthetic fixtures and development
personas. It does not provision AWS or establish hosted acceptance.

```sh
npm --prefix frontend run test:e2e
npm --prefix frontend run test:e2e -- --config playwright.journey.config.ts
git diff --check
```

The browser suites use isolated local backend fixtures. Hosted verification uses
the real application and an explicitly enrolled account; see the deployment guide.

## Current capabilities

- Catalog-driven agent creation with models, MCP connections, selected tools,
  prompts, skills and optional evaluation datasets.
- Immutable agent versions, durable deployment/invocation jobs, conversation
  persistence and ownership-checked deletion.
- Generic MCP onboarding for authenticated HTTPS Streamable HTTP endpoints:
  save authentication, discover schemas, review, approve and publish.
- Saved API-key/PAT and IAM connections, plus creation or reuse of OAuth
  providers. Per-user OAuth uses Cognito inbound authentication and native
  Gateway consent; administrators supply their own provider configuration.
- Dependency checks protect connections used by saved agents and credentials used
  by MCP registrations. Editing an unused connection requires rediscovery/review.
- New AWS Agent Registry (`agent-registry-control`) for MCP records; the Studio
  catalog in DynamoDB supplies workspace visibility and pinned execution bindings.
  Runtime agents are not automatically published as Registry AGENT records.

Snowflake is one remote MCP provider. Its native server, data objects and role
permissions are created in Snowflake, then the endpoint is onboarded through the
same generic Studio form. The full SQL and credential lifecycle are in the
[Snowflake setup guide](docs/snowflake-setup.md).

## Repository map

| Path | Responsibility |
| --- | --- |
| `frontend/src/` | Cloudscape console and generic MCP management |
| `backend/` | Authenticated API, catalog/governance, durable jobs and AWS adapters |
| `foundation_harness/`, `runtime/journey/` | Agent execution, model/tool loop, traces and results |
| `infra/` | Python-generated CloudFormation templates and scoped IAM/tag rules |
| `scripts/` | Packaging, target-bound deployment, bootstrap and audits |
| `examples/journey/` | Operator catalog/template inputs |
| `tests/`, `frontend/e2e/`, `frontend/journey-e2e/` | Contract, security and browser tests |
| `docs/` | User and operator guides and retained implementation contracts |
| `artifacts/` | Ignored target state, release receipts and sanitized validation evidence |

`backend/mcp_onboarding.py` and `backend/mcp_onboarding_cloud.py` implement generic
MCP registration. `backend/mcp_management.py` preserves read/management access to
previously registered connections and protects their saved-agent dependencies.
Snowflake provisioning scripts, creation APIs, IAM and demo templates have been
removed. Runtime response compatibility for existing Cortex Agent bindings remains.

Historical implementation contracts for foundation admission, model execution and
research remain where source/tests reference them. Their old milestone evidence
is not a statement of current deployment status. Use the current deployment guide
and the latest retained release receipt for operational decisions.
