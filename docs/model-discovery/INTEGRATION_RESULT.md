# Discovery → /api/catalog integration RESULT

- Branch: `feature/discovery-catalog-integration` (worktree `/home/ec2-user/work/agent-studio-discovery-integration`)
- Baseline: `ce84da3f613c7d70dfea14525c27d2f9f781eeea` (feature/model-discovery-module HEAD; itself on ff6d73b)
- Author: Melanie Li. NOT deployed; no cloud writes, no model invocation. Review branch only.

## What was delivered

Wired the existing recent-model discovery module (`backend/model_discovery.py`,
27 tests, previously standalone) into the real backend serving chain:
`DiscoveryCatalogSource` (cached, owner-reviewed feed adapter) →
`configured_catalog()` → `LiveCatalog` → `/api/catalog` (existing route,
verified via real FastAPI TestClient requests, not module-only unit tests).

### New files (this branch owns)
- `backend/discovery_catalog_source.py` — cache adapter:
  - Reads owner-maintained on-disk JSON discovery cache (`schema_version: 1`,
    `official_review_status: pending|complete`, https feed `source`, `records`).
  - Missing/corrupt/wrong-schema cache ⇒ `IntegrationNotConfigured` fail-loud with a
    specific message; NEVER silent empty success.
  - Unreviewed records excluded with explicit reason `unreviewed_record`; window/date/
    vendor/source-url filtering delegated to `backend/model_discovery.discover()`
    (rolling six calendar months, backend-side, day-clamped).
  - Every row carries source/review metadata: `source_url`, `release_date`, `review`
    (reviewed_by/reviewed_at/status), `official_data_status`, revision digest.
  - Separate facts recorded separately: `region_availability` / `runtime_protocol`
    default `'unknown'` (owner-reviewed strings pass through), `gateway_enumeration`
    `'NotConnected'`, `entitlement` `'unverified'`, `execution_ready`/`integration_ready`
    pinned `False`, `execution_binding` `{'status':'unverified','last_checked':None}` —
    re-pinned AFTER merging cache content (poisoned-input safe, same defect class the
    module's TDD caught).
  - `requestable=False` always: discovery never auto-creates exposure or grants.
- `tests/test_discovery_catalog_integration.py` — 10 adapter tests (all synthetic
  `synthetic-*` records, no real model names/dates).
- `tests/test_discovery_api_catalog.py` — 9 integration tests through
  `configured_catalog()` + real TestClient `/api/catalog`.
- `docs/model-discovery/INTEGRATION_RESULT.md` — this report.

### Modified files (owned by this task)
- `backend/live_catalog.py`:
  - `LiveCatalog` gains optional `discovery` provider slot; TTL cache is bypassed when
    a discovery source is configured so cache expiry/removal fails loud immediately.
  - `source_status()` adds a `Discovery` entry with `official_data_status`
    (`pending` until the owner marks the official list complete), discovered/excluded
    counts and an explicit reason.
  - `configured_catalog()` accepts optional `discovery_sources` (explicit opt-in:
    `approved: true`, `source_id`, `cache_path`, `scope.workspaces`). Enabled-but-missing
    cache fails at construction with a specific error — the optional feature is never
    silently empty. Config without the key is byte-for-byte unchanged behavior.
  - `projection()` public fields extended with `discovery_only`, `release_date`,
    `source_url`, `review`, `official_data_status`, `region_availability`,
    `runtime_protocol` (serving metadata; no new authority).

### NOT touched (parallel worker ownership respected)
- Requests worker's files: `backend/app.py`, `backend/repository.py`,
  `backend/schemas.py`, `backend/store.py`, `frontend/src/main.tsx`, ProductText.
- Catalog worker's files: `frontend/src/AICatalog.tsx`, `ModelPresentation.ts`.
- `backend/model_discovery.py` used as-is (no changes needed).
- No frontend static model table anywhere; models flow from the backend cache through
  `/api/catalog`.

`git status` confirms the only tracked modification is `backend/live_catalog.py`;
everything else is new files.

## Interface regression evidence (real TestClient, not module tests)

`tests/test_discovery_api_catalog.py` (synthetic data, labeled):
- `test_api_catalog_serves_discovery_rows_with_review_metadata` — GET `/api/catalog`
  in live mode returns the discovery row with source_url/release_date/review/
  official_data_status=pending; readiness fields all negative; `granted=False`,
  `usable=False`, `requestable=False`; `sources.Discovery.official_data_status=='pending'`;
  detail route `/api/catalog/{id}` serves the same record.
- `test_api_catalog_hides_discovery_rows_from_other_workspaces` — workspace scoping intact.
- `test_api_catalog_fails_loud_when_cache_removed_at_runtime` — 200 then cache deleted ⇒
  503 `no fixture fallback`, never a fake-empty success.
- `test_discovery_rows_not_selectable_in_builder_and_no_grant_created` —
  `/api/build-options` never offers a discovery row as a Builder model (no
  foundation binding ⇒ filtered by existing `builder_catalog.choices`); admin catalog
  shows zero `discovery:*` grants. Existing execution routes and grant flow untouched.
- `test_historical_binding_outside_window_still_resolvable` — an out-of-window record
  is absent from `/api/catalog` discovery yet `model_discovery.binding_lookup` still
  resolves it (`binding_retained=True`, `in_discovery_window=False`,
  `execution_ready=False`). Six-month rolling filter deletes nothing historical —
  the half-year-old-Haiku-binding preservation requirement.
- `configured_catalog` construction tests: explicit source accepted; missing key ⇒
  `None` (unchanged); missing cache ⇒ specific construction error; unapproved source
  rejected before any SDK access.

TDD sequence: RED#1 collection ImportError (adapter absent) → GREEN#1 10 passed →
RED#2 8 failed/1 passed (wiring absent) → GREEN#2 19 passed → full-suite regression.

## Test runs (same version, /tmp/gab/.venv python 3.13)

- `tests/test_discovery_catalog_integration.py` + `tests/test_discovery_api_catalog.py`:
  **19 passed**.
- Full suite: see "Full regression" below.

## Honest data status

- **No official model list is fabricated here.** The cache in tests is synthetic and
  labeled. The real official recent-model list (per-model official release dates, not
  ID dates or region-availability dates) is being verified by the owner separately;
  until that verified cache file is provided and approved in
  `NATIVE_CATALOG_CONFIG.discovery_sources`, the Discovery source is either absent
  (`NotConnected`, explicit reason) or `official_data_status: pending`.
- Haiku `execution_ready` remains `false` in any discovery row by construction; no
  business execution has been demonstrated and this branch does not claim otherwise.
- Integration complete ≠ live data complete: this branch proves the read chain with
  synthetic data; live population requires the owner-verified cache.

## Full regression

- Command: `/tmp/gab/.venv/bin/python -m pytest tests/ -q` in the worktree.
- Result: 1155 passed, 1 failed — `tests/test_web_research.py::test_react_renders_malicious_report_as_text`
  fails with esbuild `ERR_MODULE_NOT_FOUND` because this worktree lacks
  `frontend/node_modules`; the same test passes in the main checkout (/tmp/gab).
  Known environment difference (documented in the module-delivery skill reference),
  not a code regression: the failure predates this branch in any worktree without
  frontend deps.

## Push evidence

- Pushed `feature/discovery-catalog-integration` to origin (see commit message).
- Verified with `git ls-remote --heads origin refs/heads/feature/discovery-catalog-integration`.
- No deployment performed; deploy/activation is a separate owner decision.
