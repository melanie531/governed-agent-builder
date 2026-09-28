# Agent Studio

A governed agent builder with a React/Cloudscape console, a FastAPI backend, and
AWS deployment on CloudFront, Cognito, API Gateway, Lambda, DynamoDB and SQS.
Business agents run on Amazon Bedrock AgentCore Runtime and call selected MCP
tools through AgentCore Gateway. Administrators onboard remote MCP endpoints,
review discovered tools, and publish them through AWS Agent Registry.

## Start here

- [Deploy and update the application](docs/deployment.md)
- [Set up Snowflake managed MCP, including SQL and PATs](docs/snowflake-managed-mcp.md)
- [Onboard and manage any supported MCP endpoint](docs/generic-mcp-onboarding.md)
- [Agent creation, runtime, conversation and deletion contracts](docs/create-agent-journey.md)
- [Serverless architecture and identity boundaries](docs/SERVERLESS-HOSTING.md)

The current application is https://d11jko88tox2zk.cloudfront.net in AWS account
`250708454815`, region `us-east-1`. Deployment commands require an explicit account,
profile, region and target state; these identifiers are not credentials.

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
- Saved API-key/PAT credential management. Existing OAuth-client-credentials and
  IAM configurations can be reused; the PAT form does not create OAuth providers.
- Dependency checks protect connections used by saved agents and credentials used
  by MCP registrations. Editing an unused connection requires rediscovery/review.
- New AWS Agent Registry (`agent-registry-control`) for MCP records; the Studio
  catalog in DynamoDB supplies workspace visibility and pinned execution bindings.
  Runtime agents are not automatically published as Registry AGENT records.

Snowflake is one remote MCP provider. Its native server, data objects and role
permissions are created in Snowflake, then the endpoint is onboarded through the
same generic Studio form. The full SQL and credential lifecycle are in the
[Snowflake runbook](docs/snowflake-managed-mcp.md).

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
| `docs/` | Operator guides and retained implementation contracts |
| `artifacts/` | Ignored target state, release receipts and sanitized validation evidence |

`backend/mcp_servers.py`, `backend/mcp_cloud.py` and `infra/mcp_servers.py` retain
compatibility for previously provisioned Snowflake connections and shared
onboarding behavior. They are still used and tested. The unused Snowflake-only
frontend and separate preview deployment path have been removed.

Historical implementation contracts for foundation admission, model execution and
research remain where source/tests reference them. Their old milestone evidence
is not a statement of current deployment status. Use the current deployment guide
and the latest retained release receipt for operational decisions.
