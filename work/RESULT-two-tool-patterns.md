# Result: two governed tool-integration patterns (LOCAL SIMULATION)

Branch: `feat/tool-agent-as-tool-and-builtin-snowflake` (pushed; no PR, merge or deploy).

## What changed
- `backend/catalog.py` — two new fixture catalog `tool` components, composed only by the
  Research foundation and **not** in initial grants (admin grant or approved request required):
  - `agent-risk-analyst` — agent-as-tool (`tool_type: agent`), specialist pinned at v1.
  - `snowflake-approved-views` — builtin MCP Snowflake connector (`tool_type: builtin-mcp-snowflake`).
  - Each carries `use_when` selection guidance: multi-step business reasoning → agent-as-tool;
    pure retrieval of pre-approved data → Snowflake connector.
- `backend/harness.py` — `call_tool()` simulates the Tool Gateway MCP `tools/call` boundary
  locally (deterministic, stdlib only, still a single portable file, so the export is unchanged):
  - runs only tools selected in the saved manifest, with a matching pinned version;
  - agent tool accepts only `{question}`; `role`, `tenant_id` or other extra arguments are rejected;
  - Snowflake accepts only `{query_id}` from a whitelist of named queries over approved read-only
    views; no SQL argument and no `execute_sql`; returns masked synthetic rows;
  - denied calls are recorded in the trace (`tools_call` → `denied`), never executed.
- Governance is unchanged and reused: `validate_definition` checks grants, foundation
  compatibility and pinned versions on create, every job step and every invoke, so revocation
  blocks later calls (tested).
- `docs/INTEGRATIONS.md` — section describing both patterns plus documentation-only placeholders
  `SNOWFLAKE_MCP_SECRET_ARN=<approved-secret-store-reference>` and
  `SNOWFLAKE_MCP_SERVICE_ROLE=<least-privilege-read-only-role>`.
- `tests/test_two_tool_patterns.py` — 17 focused tests.

## Verification (commands run)
- `uv run --locked --extra foundation-runtime pytest -q` → **1682 passed, 4 skipped, 0 failed**.
  Baseline before changes: 1664 passed plus 1 failure caused by the environment
  (`test_react_renders_malicious_report_as_text`: `frontend/node_modules` was not installed).
  After `npm --prefix frontend ci` it passes.
- `tests/test_two_tool_patterns.py`: 17 passed. Covers (a) agent-as-tool grant → invoke,
  revoke → 403, identity/extra arguments rejected, version pin required; (b) Snowflake
  rejects generic SQL, `execute_sql`, SQL-as-query_id, SQL alongside a valid query and unlisted
  queries; (c) both are catalog tools that are requestable and unusable until granted, and
  absent from build options and agent creation (403) without a grant.
- `EXECUTION_MODE=aws`: `tests/test_workflow.py -k aws` passes, and a direct `create_app`
  call raises `RuntimeError: AWS mode disabled ... No simulation fallback.`
- `npm --prefix frontend run build` plus Playwright fixture config **58 passed** and journey
  config **10 passed**.

## Assumptions
- "Local simulation catalog" means the fixture catalog (`backend/catalog.py` + `harness.py`),
  not the cloud Journey catalog (`journey_catalog.py`, Gateway MCP servers), which would need
  real Gateway targets.
- The specialist agent is a seeded synthetic catalog record. Registering an agent built in the
  Studio as a tool (a publish flow) is not implemented.
- Pinning uses the catalog component version, which equals the specialist agent version. An
  admin catalog revision (v2) therefore requires a matching published specialist version;
  until one exists, calls are denied (`AGENT_VERSION_NOT_PINNED`).
- The builtin Snowflake connector's real AgentCore target shape and auth flow were not
  verified. The simulation models only the governance contract.

## Open questions (v1) — all resolved in v2
1. Agent-as-tool / A2A: **resolved, no A2A.** The specialist is an MCP tool served through the
   Tool Gateway (tools/list, tools/call), like any other MCP tool.
2. Studio agents vs specialists: **resolved.** Specialists are platform-curated only (admin-owned
   catalog entries). Builder-created agents are directly usable and need no publish step.
3. Nested governance: **decided (delegated to us).** The specialist's own manifest and service
   identity govern its inner tools. The caller's grant only governs whether the caller may call
   the specialist tool. No inheritance or intersection of caller tool grants.
4. Real whitelist and service-role ownership: now tracked as the SSM inputs below.

---

# v2 (2026-09-25): Melanie's corrections + standalone Snowflake smoke

## What changed in v2
- **Specialist is an MCP tool via Gateway, not A2A.** `backend/harness.py` adds `list_tools()` (a local
  MCP `tools/list` via Gateway that returns manifest-selected tools with strict `inputSchema`s),
  and `call_tool()` is documented as MCP `tools/call` via Gateway. Every result and trace call entry
  carries `"via": "mcp-tool-via-gateway"`. Catalog `agent-risk-analyst` is renamed
  "Risk analyst specialist (MCP tool via Gateway)", with `tool_type: mcp-tool-via-gateway`,
  `curation: platform-curated`, provider "Platform-curated specialist (admin-owned)" and protocol
  "MCP tool via Tool Gateway (locally simulated)". The Snowflake protocol also names the Tool Gateway.
  Component ids and versions are unchanged, so existing grants and pins still apply.
- **Specialist = platform-curated only; builder agent = directly usable.** Builder agents need no
  specialist or publish step. They cannot be referenced as MCP tools in another manifest. The API
  rejects this, and `call_tool` returns `TOOL_NOT_IN_MANIFEST`.
- **Nested governance.** A specialist entry has its own `manifest` and `service_identity`. Its inner
  calls (Snowflake `open_incident_counts`) run through `call_tool` under that manifest and are traced
  with `identity: synthetic-svc-risk-analyst`. A caller with no Snowflake grant still gets the
  specialist result. A caller that *does* hold Snowflake cannot lend it: if the specialist's own
  manifest disallows the inner tool, the call fails closed with `SPECIALIST_INNER_TOOL_DENIED`.
- **`scripts/snowflake_readonly_smoke.py`** is new: a standalone, opt-in operator smoke. It is
  not imported by `backend/` or `runtime/` (a test enforces this). Guardrails:
  - refuses unless `SNOWFLAKE_SMOKE=1`;
  - refuses unless all 8 `SNOWFLAKE_SMOKE_SSM_*` env vars are set, and each must look like an SSM
    parameter *name* (`/path`), so values or secrets passed in env are rejected;
  - `--query` must be a plain uppercase identifier, so raw SQL is rejected before any SSM read;
  - it reads all values (with decryption) from SSM at run time. The whitelist of approved views is
    itself an SSM parameter (a JSON list of identifiers). Role, warehouse, database and schema must
    be plain identifiers;
  - it runs exactly one statement shape, `SELECT * FROM "<DB>"."<SCHEMA>"."<APPROVED_VIEW>" LIMIT 5`,
    and only for a whitelisted view;
  - output is counts only (`row_count`, `column_count`, `row_limit`). Errors report only the
    exception type, because driver or AWS messages can contain account or user identifiers;
  - auth uses a key pair (PEM private key from SSM, converted to DER for `snowflake.connector.connect`).
    The connector is **not** a project dependency; you run it with
    `uv run --with snowflake-connector-python`.
  - It was **not** run against Snowflake in this session (no credentials).
- `docs/INTEGRATIONS.md` section is rewritten for the above.
- Tests: `tests/test_two_tool_patterns.py` gains 4 tests (MCP-via-Gateway naming and tools/list contract,
  builder agent directly usable, nested governance with the specialist's own identity, and no
  caller-grant inheritance). `tests/test_snowflake_readonly_smoke.py` is new, with 25 mocked tests
  (SSM and driver are fakes; `boto3.client` is forbidden in these tests).

## v2 verification (commands run)
- Baseline on 3b1b745: `uv run --locked --extra foundation-runtime pytest -q` → 1682 passed, 4 skipped.
- After v2: same command → **1711 passed, 4 skipped, 0 failed** (+29 new).
- `EXECUTION_MODE=aws`: `tests/test_workflow.py -k aws` passes, and `create_app()` still raises
  `RuntimeError: AWS mode disabled ... No simulation fallback.`
- A diff scan finds no Snowflake hostnames, account-like ids or PEM blocks. The repo contains only
  `SYN_*` / `/synthetic/...` values.
- The frontend is untouched in v2, so the Playwright suites were not re-run.

## Assumptions / caveats (v2)
- The `snowflake.connector.connect(private_key=<DER bytes>)` key-pair pattern follows Snowflake's
  documented Python connector usage. I have not verified it against a real account or a pinned
  connector version.
- Only key-pair auth is implemented. If Melanie provides a programmatic access token instead, the
  `snowflake_connect` function needs a small change.
- The smoke validates that the least-privilege role can read the whitelisted views. It does not
  run negative (write) probes, because that would require non-whitelisted SQL.

## What I need from Melanie to run the real Snowflake smoke
Create these SSM parameters (suggested names; any `/path` works because the script takes each
name from its env var). Use `SecureString` for the key and prefer it for all of them:

| Env var the script reads | Suggested SSM parameter name | Value |
|---|---|---|
| `SNOWFLAKE_SMOKE_SSM_ACCOUNT` | `/governed-agent-builder/snowflake-smoke/account` | account identifier (`<org>-<account>`) |
| `SNOWFLAKE_SMOKE_SSM_USER` | `/governed-agent-builder/snowflake-smoke/user` | least-privilege service user (key-pair auth, no password) |
| `SNOWFLAKE_SMOKE_SSM_PRIVATE_KEY` | `/governed-agent-builder/snowflake-smoke/private-key` | unencrypted PKCS#8 PEM private key for that user (**SecureString**) |
| `SNOWFLAKE_SMOKE_SSM_ROLE` | `/governed-agent-builder/snowflake-smoke/role` | role with `USAGE` on warehouse/db/schema + `SELECT` on the approved views only |
| `SNOWFLAKE_SMOKE_SSM_WAREHOUSE` | `/governed-agent-builder/snowflake-smoke/warehouse` | warehouse name |
| `SNOWFLAKE_SMOKE_SSM_DATABASE` | `/governed-agent-builder/snowflake-smoke/database` | database name |
| `SNOWFLAKE_SMOKE_SSM_SCHEMA` | `/governed-agent-builder/snowflake-smoke/schema` | schema holding the approved views |
| `SNOWFLAKE_SMOKE_SSM_APPROVED_VIEWS` | `/governed-agent-builder/snowflake-smoke/approved-views` | JSON list of exact uppercase view names to whitelist, e.g. `["VIEW_A","VIEW_B"]` |

Also needed:
- the AWS account/region holding the parameters, and an operator role with `ssm:GetParameter`
  (plus `kms:Decrypt` on the key) scoped to `/governed-agent-builder/snowflake-smoke/*`;
- explicit go-ahead to run `SNOWFLAKE_SMOKE=1 ... uv run --with snowflake-connector-python python
  scripts/snowflake_readonly_smoke.py --query <VIEW>` once per approved view.
