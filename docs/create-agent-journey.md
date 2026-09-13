# Create Agent journey

The `feat/create-agent-journey` branch adds a Catalog-driven business builder with
Research and Knowledge Q&A templates. The feature is enabled by the platform's
`JOURNEY_ENABLED=1` deployment configuration.

## Catalog and MCP selection

`GET /api/catalog` supplies both the Catalog page and the builder. Platform-owned
records in the shared repository contain model, MCP server, child tool, and skill
metadata. Private execution bindings are never returned to the browser.

Business users select MCP servers. The default view shows connections; a collapsed
permissions section contains each selected server's tools. Platform-recommended
tools are initially selected. Only explicitly selected tool IDs are compiled into
the saved manifest; adding tools to a server later does not expand existing agents.

`examples/journey/*.json` are operator publication inputs, not application runtime
constants. `POST /api/journey/catalog/skills` is a platform-admin-only publication
API with optimistic version checking. Workspace grants remain separate from
publication.

The Tool requests page accepts requests for new tools missing from the Catalog.
Business users see their own requests and administrator responses. Administrators
respond under Policies & approvals; responding never grants or publishes a
capability. Access requests for existing models, MCP servers and skills stay in
their Catalog detail panels.

## Runtime and evaluation

`backend/journey.py` implements durable deployment, invocation, and evaluation jobs.
The existing Cognito BFF, DynamoDB repository, stream dispatcher, and SQS worker
provide identity and persistence. External AWS calls are outside database
transactions. A saved definition pins its Catalog versions, bindings, Foundation
ZIP version, and manifest digest.

`runtime/journey/main.py` runs the platform Foundation on AgentCore Runtime.
`foundation_harness/journey_runtime.py` executes Bedrock Converse and only the
selected Gateway MCP operations. The native Runtime accepts user input and a
request identifier; callers cannot replace configuration or credentials.

Without a dataset, deployment performs its health check and records evaluation as
`SKIPPED`, with no evaluation job or Evaluate call. With a dataset, each case gets
an independent Runtime session. Actual execution spans and reference answers are
submitted to AgentCore `Builtin.Correctness`. Native evaluation results are stored
separately from deployment status. An uncertain paid request is not automatically
replayed.

The Knowledge MCP target intentionally serves synthetic Aurora documents. Replace
it through platform publication when connecting a real business knowledge source.
The current Runtime network mode is managed `PUBLIC`; inbound access remains IAM
authenticated. This release does not provision customer VPC endpoints or Cedar
policies.

## Chat, API calls and deletion

The agent detail page supports conversation after deployment. Conversation IDs
are bound to the owner, agent and immutable version. Follow-up messages receive
the previous three complete turns from server storage; the latest ten turns are
displayed and survive reload. Starting a new conversation clears that context.
Each turn has a new execution receipt and trace, and uses the saved model and
Gateway tool permissions. Failed or uncertain turns are not silently replayed.

The API access tab provides a Python `boto3` example with the actual Runtime ARN.
The caller uses its own AWS credentials with `bedrock-agentcore:InvokeAgentRuntime`
permission on that ARN. Native API calls remain subject to the immutable Runtime
manifest. Browser chat uses the signed-in business identity and CSRF protection:
`POST /api/journey/agents/{id}/invoke` accepts input, version, idempotency key and an
optional conversation ID; `GET /api/journey/jobs/{job_id}` returns the result.

Deletion requires a separate review request and explicit confirmation. The user
types the exact agent name; a five-minute one-time confirmation token binds the
agent, saved version, owner and signed-in session. The backend rejects direct,
expired or mismatched confirmations. Active operations must finish before
deletion can start. Cleanup can also remove failed deployments and agents whose
Catalog grants were revoked.

The durable cleanup job finds only the agent's server-derived Runtime names,
verifies ownership tags and the execution role, deletes every Runtime version,
waits for deletion, then removes versioned manifests, execution/evaluation
objects, agent log streams, chat and saved definition data. Shared Gateway
targets, credentials, roles, Foundation ZIPs and Catalog records are retained.
An audit tombstone records the cleanup outcome. Failed cleanup is visible and
can be reviewed and confirmed again; it is never reported as successful deletion.

## Target-bound deployment

Use the already bound target state. Every AWS command checks STS account, region,
stack ownership, and existing output identities. Never rely on the default profile.

```sh
.venv/bin/python scripts/serverless_package.py
.venv/bin/python scripts/journey_platform.py prepare \
  --expected-account 820242898417 --profile account-820 --region us-west-2 \
  --state artifacts/account-820242898417-us-west-2/release-state.json
.venv/bin/python scripts/journey_platform.py activate \
  --expected-account 820242898417 --profile account-820 --region us-west-2 \
  --state artifacts/account-820242898417-us-west-2/release-state.json
npm --prefix frontend run build
.venv/bin/python scripts/journey_platform.py publish-ui \
  --expected-account 820242898417 --profile account-820 --region us-west-2 \
  --state artifacts/account-820242898417-us-west-2/release-state.json
```

Initial preparation requires `TAVILY_API_KEY` in the operator process environment.
It is sent directly to AgentCore Identity; no value is written into Catalog data,
Lambda environment variables, manifests, source, or logs. Preparation creates only
project-owned resources, verifies actual Gateway tool calls, checks model
execution, and publishes ready Catalog records. Existing external Gateways are
not modified.

Use the journey deployment commands for this feature; the original serverless
deployment command produces the legacy default template without enabling it.

If large ZIP uploads time out on the operator network, run
`scripts/journey_delta_upload.py` with the same target arguments and
`--base-key releases/<previous-lambda-sha256>/lambda.zip`, then resume `prepare`.
This requires a prior release with identical locked dependencies. A temporary
Lambda reuses only matching compressed entries and verifies that both complete
ZIPs exactly match the local SHA-256 digests. Its IAM permissions cover only the
specified input/output objects. The helper stack and source patch are removed
after use.

## Verification

```sh
.venv/bin/python -m pytest -q
npm --prefix frontend run build
npx --prefix frontend playwright test --config frontend/playwright.config.ts
npx --prefix frontend playwright test --config frontend/playwright.journey.config.ts
```

The dedicated journey browser suite uses a real local backend with an explicitly
offline cloud adapter under `tests/`; it does not establish cloud functionality.
The ordinary suite preserves legacy behavior when the journey feature is disabled.

The separate `frontend/journey-e2e/live.mjs` script drives the real hosted UI,
Cognito login, Catalog selections, deployment, dataset evaluation, refresh and
two-turn conversation. It also calls the native Runtime API and confirms deletion,
then checks that Runtime and versioned storage objects are absent. It does not
intercept API responses. Provision a temporary business QA
identity with `scripts/journey_qa_identity.py`, then pass its generated enrollment
file to `scripts/serverless_package.py --qa-enrollments <path>` before deployment.
The existing protected QA mechanism keeps the test email unverified and binds the
exact subject to this app and workspace for 24 hours. It does not change a user's
password or email verification.

```sh
GAB_RELEASE_STATE="$PWD/artifacts/account-820242898417-us-west-2/release-state.json" \
  node frontend/journey-e2e/live.mjs
```

Credentials are read from the exact target-account SSM paths into process memory.
The runner writes no authentication trace or browser storage state. Evidence goes
to ignored `artifacts/journey-live/`; successful receipts include Runtime identity,
model ID, actual Gateway calls, and native evaluation request IDs.
