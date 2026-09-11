# Governed Agent Builder

## Portable cloud source handoff

For the current guarded serverless deployment, start with
[the target-account handoff](docs/TARGET-ACCOUNT-HANDOFF.md). It specifies exact
source/ZIP verification, Linux environment setup, explicit target-account inputs,
fresh identity resources and the remaining human login acceptance gate.
The reported real sign-in failure is **not confirmed fixed**. Historical deployment
documents are intentionally omitted from source ZIPs. The local prototype guide
and original architecture tables below describe the local mode, not cloud readiness.

A working **local interactive prototype**, not a cloud deployment. A business user selects a maintained harness foundation, chooses authorized capabilities, writes their own prompt and evaluation, creates an immutable version, and runs a real asynchronous fixture evaluation. A platform administrator owns catalog approvals, grants, mandatory gates and audit.

**LOCAL SIMULATION throughout. Fixture runner, no live LLM.** No AWS resources, paid model calls, real Runtime ARNs, CloudWatch logs, production identity, or production quality claims.

## Start with one command

Prerequisites: Python 3.12+, Node.js 20.19+ or 22.12+, npm, and [uv](https://docs.astral.sh/uv/).

```bash
./scripts/start.sh
```

Open `http://127.0.0.1:5187`. The script synchronizes locked dependencies, builds the UI, explicitly enables the local demo, and starts one loopback-bound FastAPI process with an asynchronous worker. SQLite persists under `artifacts/state.sqlite`. Re-running does not reset grants or data. Stop the foreground process with Ctrl-C.

For an operator-owned local portal:

```bash
PORT=5187 PUBLIC_URL=http://127.0.0.1:59374/ ./scripts/start.sh
```

Both API (`/api`) and built frontend are served on the same origin. PUBLIC_URL allows the exact loopback portal origin for CSRF/Host checks. It contains no credential. Open the **OpenClaw Portals entry** when launched by the operator. No public listener, unauthenticated public API, CORS wildcard, or arbitrary forward-header identity trust is enabled. The two bootstrap routes expose only synthetic personas and establish a DEV ONLY session; all data APIs require authorization.

## Official AWS UI framework

The interface uses the actual **[Cloudscape Design System](https://cloudscape.design/)** React component library, created for and used by AWS. This is not an AWS service or official AWS-branded application. Versions are pinned in `frontend/package-lock.json`; see the version table below. It imports `@cloudscape-design/global-styles/index.css` and uses AppLayout, TopNavigation, SideNavigation, BreadcrumbGroup, ContentLayout, Wizard, Cards, FormField, Select, Multiselect, Textarea, Input, FileUpload, Table, TextFilter, CollectionPreferences, Tabs, StatusIndicator, ProgressBar, Alert, and Flashbar. Cloudscape owns spacing, typography and responsive behavior. Two CSS utility rules wrap evidence text and digests. Default is light mode.

| Package | Installed version |
|---|---|
| `@cloudscape-design/components` | `3.0.1371` |
| `@cloudscape-design/global-styles` | `1.0.68` |
| `react` | `19.3.0` |
| `vite` | `6.4.3` |


## Walk the complete business journey

1. **Enter as Alex Morgan** (DEV ONLY). Alex belongs to Research studio and can use the two Bedrock demo routes, the synthetic knowledge tool and citation/concise skills.
2. **Create agent** opens a four-step Cloudscape wizard: foundation → capabilities → instructions and evaluation → review.
3. Choose **Research brief**. Choose **Claude · Bedrock**, **Synthetic knowledge search**, and optionally **Evidence citations**.
4. Give it a name. Edit the prompt, natural-language success criteria, and JSON dataset. Defaults include a grounded launch answer, a support answer, and a refusal for unknown private information.
5. Choose **Local deterministic checks**, then **Deploy & test locally**. The API saves a version and returns `202` with a durable job ID; the worker executes real bounded fixture cases. The UI polls actual state. Very fast local jobs may finish before the first poll; every stage remains in the execution trace.
6. Inspect **Evaluation results**. Scores are calculated from recorded per-check evidence, not fixed. With the defaults, all deterministic checks pass. This unlocks **Run locally** for the current version only.
7. Use **Revise agent**, change a required term or use **Try a failing case**, and retest. The prior immutable version remains. Failure prevents invocation; previous evidence cannot unlock the new version.
8. Set **Require LLM judge** to demonstrate missing evidence. Deterministic cases may score 100%, but the gate is **Needs changes** because the required judge did not run.
9. **Export source** downloads a backend-authorized ZIP with definition, prompt, dataset, rubric, pinned manifest, harness source, config, runner, requirements, README and project dependency lock. Extract and run `python3 run.py`. The portable fixture runner uses only Python's standard library.
10. Request **Synthetic strategy insights** under Capability requests. Switch to **Platform Admin · DEV ONLY**, open Policies & approvals, give a reason and approve. Switch back to Alex: the request is approved and the existing tool becomes selectable for Research brief, but not Knowledge Q&A. No connector was automatically created.
11. As admin, revoke a tool grant or catalog approval. An old passing agent now fails authorization on invoke and retry. Updating mandatory policy invalidates prior gate evidence until retested.
12. Switch to **Sam Taylor**. Operations desk has only Claude and a narrower skill grant, cannot access Alex's agents/jobs/exports/invocations, and cannot be granted external Gemini because its workspace forbids external data egress.

### Fixture semantics, not LLM semantics

`backend/harness.py` matches input keywords against three synthetic records and applies explicit instructions:

- `cite sources` or the citations skill includes real matched synthetic source IDs.
- `no citations` suppresses citations and will fail citation-required cases.
- `refuse unknown` produces an explicit refusal without evidence; `do not refuse` disables it.
- `uppercase` changes actual output casing.
- The concise skill limits answer text to 160 characters.
- The output-format configuration selects text or JSON. Test expectations are independent of this setting.

All other prompt prose and natural-language rubric text are saved without pretending a language model interpreted them. Expected terms never feed the answer generator. Checks measure required terms, format, source citations backed by retrieved evidence, and refusal behavior. Safety evidence/refusal checks remain mandatory even when a score threshold is lowered. Any required LLM judge is unavailable and blocks release.

Model route aliases currently exercise the same local harness. They demonstrate catalog and authorization behavior, not differences between real providers. Tools are local Python fixture lookups, not a live MCP transport. Skills are instruction manifests, not execution endpoints. No arbitrary URL, shell command, Python expression, or uploaded code is evaluated.

## Architecture: implemented versus planned

| Area | Implemented now | Integration gap / future direction |
|---|---|---|
| UI | React + TypeScript + official Cloudscape, built with Vite; same-origin static assets | CloudFront frontend hosting deliberately out of scope |
| Backend | Python FastAPI Catalog API, composer, authz and asynchronous worker | Hosting undecided; Fargate + SQS is a possible future hosting/queue choice, not deployed |
| State | SQLite adapter with WAL, transactions, immutable definitions, jobs/results/events, grants, policy versions, audit | DynamoDB adapter possible; not an IAM engine, Registry clone or model call path |
| Foundations | Separate versioned manifests with compatibility, config schema and mandatory defaults | Approved artifact build/signing/digest provenance beyond this checked-in harness |
| Model catalog | Seeded Claude/Bedrock OpenAI/external Gemini **demo aliases** | Separate **AgentCore Model Gateway** adapter; real model IDs, region access, protocol and authentication must be verified |
| Tool / skill registry | Separate approved seeded metadata; backend-filtered intersection | Live AgentCore Registry/tool catalog discovery not connected; foundations and models never live in Registry |
| Runtime | Real local fixture execution; no cloud runtime binding | `AgentCoreRuntimeAdapter` has an offline SDK-validated invoke entry point only. Create/update, readiness, immutable targeting, digest confirmation, locking and IAM are pending |
| Two Gateways | Distinct Model and Tool integration boundaries | Two AgentCore Gateway instances, not a single gateway or API Gateway substitute. Target discovery, supported protocols and model route integration remain unimplemented |
| Evaluation | Actual deterministic cases and measured evidence, immutable version/dataset/policy binding | AgentCore Evaluations / LLM judges / CloudWatch correlation not connected |
| Identity | Explicit DEV ONLY persona sessions, server-stored canonical identities, HttpOnly SameSite cookie, CSRF + origin checks | Production Okta OIDC integration not implemented; non-demo startup fails closed |
| AgentCore Identity | Documented downstream identity/credential role only | Does not convert Okta identity into IAM grants; not connected |
| Observability | Local durable execution events and audit, no chain of thought | CloudWatch → AgentCore Evaluations → backend results; optional existing Splunk export is future work |
| Cloud scope | None | One future AWS account, scoped per-agent/version deployments; never one globally shared runtime |

```text
Cloudscape UI → Python Catalog API / Composer → SQLite immutable definition
                       │
                       ├─ Foundation Library (harness manifests)
                       ├─ Model Gateway catalog boundary (demo routes now)
                       └─ Tool / Skill Registry (local seed now)

Deploy & Test → durable job → local worker → fixture output → checks → version gate
Admin grant/revoke/policy → current authorization rechecked at every transition and run
```

## Security boundaries and authorization

- A persona's ID comes only from a random server-stored session. All roles and workspace attributes resolve from the server catalog. Client payload identity fields are forbidden. Forged role headers are ignored.
- **Every** agent list/create/read/revision/deploy/job-result/export/invoke endpoint is authorized. Business users see only their own workspace-owned records. Admin governance does not imply permission to read private prompts/datasets. Capability requests are owner-scoped unless the session is admin.
- The local selector deliberately lets the operator choose any synthetic identity. **It is not security between real people.** Everyone able to use this local demo can choose Admin. Never host this demo for untrusted users or enter real data.
- Session rotation invalidates the prior cookie. Cookies are HttpOnly, SameSite=Strict, eight-hour expiry, Secure when PUBLIC_URL uses HTTPS. Mutations require an exact approved Origin; authenticated mutations additionally require a session-bound CSRF token. Host allowlisting protects against DNS rebinding. No forwarded role or membership is trusted.
- Selection authorization is approval ∩ identity/workspace entitlement ∩ foundation compatibility ∩ external data policy. The backend checks stable IDs, kind, versions and exact version-map keys. Changing foundation/model clears UI dependent selections, but API validation is the security boundary.
- Worker transitions and invocations revalidate current permissions. Grant revocation immediately blocks old agents and retries. Catalog revisions require a new definition; gate-policy revisions require retesting. The latest current-version test must pass before invocation.
- Export is authorized for the owner but deliberately available for failed drafts or after capability revocation: it contains synthetic configuration and standard-library source, no provider access. An exported offline copy is outside platform policy enforcement.
- Strict JSON schemas forbid extra fields, duplicate case IDs and executable fields. Requests are capped at 64 KiB; uploads at 32 KiB in UI. 20 cases/job, 2 concurrent jobs, 8 queued jobs, 60-second deadline, 30 jobs/identity/hour, 30 invocations/identity/minute, 100 agents/identity. No cloud cost in local mode.
- SQLite is local synthetic-only storage. Production encryption at rest, tenant-aware storage policies, backups, retention and stronger audit integrity are not implemented. Application-level audit is not tamper-proof against the filesystem owner.
- Start through `python -m backend` or the supplied script: the supported launcher enforces loopback and one Uvicorn process. Do not run a public reverse proxy or override the server with a different launcher. The operator portal is local-only.
- Provider credentials never appear in browser bundles, exports, datasets or logs. This app does not collect credentials. An approved future SDK adapter would use server-side workload identity and the AWS SDK credential chain, not a browser key field.

## Durable execution and operational limits

Definitions are inserted transactionally. Revisions compare `base_version` to current version and append; jobs bind agent/version/digest/dataset and requester. Idempotency keys are unique per agent/requester and cannot be reused for a different version. An active job prevents another job on the same agent. Unrelated agents can execute concurrently.

The supported deployment is **one API process with one event-driven worker and two concurrent execution slots**, not a distributed queue. On startup, nonterminal durable jobs resume from validation and retain the original deadline. Fixture execution has no side effects, so replay cannot create duplicate external resources. Results and final status persist transactionally. A crash may leave intermediate repeated events, labelled with recovery. This is not a distributed lease or exactly-once AWS deployment guarantee. The SQLite adapter is replaceable at the `Store` boundary, though repository queries still use SQL; a DynamoDB port requires repository implementations, not a configuration toggle.

## Future Okta and AWS setup (not enabled)

See [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) for an explicit readiness checklist and placeholder-only Okta/IAM mapping. `EXECUTION_MODE=aws` fails at startup with actionable missing integrations. Turning off DEMO_MODE also fails until production identity is implemented. There is no fallback from real mode to simulation.

`backend/aws_adapter.py` provides a real boto3 `bedrock-agentcore.invoke_agent_runtime` entry point, **offline shape-tested against the installed SDK only**. It requires explicit region, approved Runtime ARN, qualifier, two different HTTPS Gateway endpoints, and `allow_aws_calls=True`; none are configured by the demo. No live AWS or paid model tests were performed. Integration tests remain intentionally disabled; do not enable without user approval, identity and quota configuration.

## Verify

```bash
uv sync --locked
uv run --locked pytest -q
npm --prefix frontend ci
npm --prefix frontend run build
npm --prefix frontend run test:e2e
```

Browser tests start an isolated loopback server and use the installed Google Chrome channel. To use a Playwright-managed Chromium already present on another system, set `PLAYWRIGHT_CHANNEL=chromium`; configuration maps it to the bundled browser. No large browser download is part of startup. Browser-test SQLite and screenshots live only in ignored `artifacts/`.

Backend tests cover the route auth matrix, client identity forgery, component-kind/version forgery, foundation compatibility, cross-workspace agent/job/export/invoke access, CSRF and cookies, revocation, stale evidence, deterministic pass/fail/missing-judge gates, restart/idempotency, capability approval/rejection, export execution and offline AWS SDK shapes. See [docs/VERIFICATION.md](docs/VERIFICATION.md) for measured run results and remaining limitations.

The GitHub workflow runs backend tests and the frontend build only. No cloud deployment, CI/dev/prod promotion, or release workflow exists.

## Well-Architected review of this prototype

| Pillar | Current evidence | Remaining production obligation |
|---|---|---|
| Security | Fail-closed mode, server authz, current grants, CSRF, bounded synthetic inputs | Real Okta, least-privilege IAM/PassRole, encryption, TLS, secrets/egress controls |
| Reliability | Transactional versions, durable restart, bounded jobs, idempotency tests | Distributed leases/queues, deployment reconciliation, backups, recovery drills |
| Performance | Async 202 flow, two local slots, no model wait | Real load tests, runtime cold starts, observed latency |
| Cost | Zero cloud/model calls, hard local request/case/rate caps | Model token budgets and account/service quotas |
| Operational excellence | Real local trace/audit, explicit gates, source export, tests | CloudWatch/Evaluations correlation, alarms, Splunk integration |
| Sustainability | Reusable harness and idempotent local jobs | Cloud right-sizing and utilization measurement |
