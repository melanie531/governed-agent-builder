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

## Open questions for Melanie
1. Should agent-as-tool also appear in the cloud Journey catalog (as an MCP server with a
   child tool, or as an A2A `agent` record from Registry)? That needs a real Gateway target.
2. Should Studio-published agents be registrable as tools (a publish → admin approve → catalog
   flow), or are specialists platform-curated only?
3. Nested governance: when a specialist agent calls its own tools, should the caller's grants
   also be intersected, or does the specialist's own manifest and identity suffice?
4. Which real approved views and named queries should the Snowflake whitelist contain, and who
   owns the least-privilege service role and secret?
