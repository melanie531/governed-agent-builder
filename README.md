# Agent Studio

A governed agent builder with a React/Cloudscape console, a FastAPI backend, and
AWS deployment on CloudFront, Cognito, API Gateway, Lambda, DynamoDB and SQS.
Business agents run on Amazon Bedrock AgentCore Runtime and call selected MCP
tools through AgentCore Gateway. Administrators onboard remote MCP endpoints,
review discovered tools, and publish them through AWS Agent Registry.

## Start here

- [User guide: configure an MCP connection, create an agent and test its tools](docs/start-here.md)
- [Deploy and update the application](docs/deployment.md)
- [Snowflake setup: managed MCP with PAT or 3LO, Python MCP upload, and agent testing](docs/snowflake-setup.md)
- [Onboard and manage any supported MCP endpoint](docs/generic-mcp-onboarding.md)
- [Agent creation, runtime, conversation and deletion contracts](docs/create-agent-journey.md)
- [Serverless architecture and identity boundaries](docs/SERVERLESS-HOSTING.md)

A new installation starts with an empty AgentCore Gateway and AWS Agent Registry.
It creates no remote MCP connection or provider credential. Administrators publish
models and configure their own endpoints and authentication in Studio. Snowflake
is an optional manual integration, not part of platform deployment.

Deployment commands require your explicit account, profile, region and private
target-state path. Never copy another installation's state or credentials.

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
- Saved API-key/PAT and IAM connections, plus generic references to existing
  OAuth providers. Per-user OAuth uses Cognito inbound authentication and native
  Gateway consent; the platform does not create provider examples automatically.
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
