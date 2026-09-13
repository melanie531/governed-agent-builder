# Production discovery handoff — review, not deployed

Fixes on top of 1bff34a:
- Remove vendor-to-endpoint inference and vendor allowlist. Per-model supported_endpoints are retained from reviewed official evidence. provider_api stays unconfigured; no endpoint is invoked and no grant/readiness is granted.
- scripts/build_discovery_cache.py joins the real 113-model API snapshot to owner-provided official-date inventory by exact IDs/explicit document aliases. Missing/ambiguous/insufficient precision becomes pending. It does not prefilter by a fixed date: DiscoveryCatalogSource performs the rolling window at read time.
- backend/reviewed_model_discovery_cache.json is the generated reviewed cache with sources and original API retrieval timestamp.
- scripts/discovery_config_overlay.py adds discovery_sources to an EXISTING native source configuration; deep-copy overlay preserves registries, model gateways and all unrelated settings. Explicit existing approved workspace scope is required. No blank/new replacement native config.

Real data through production factory test:
configured_catalog(config overlay, mocked STS identity only) -> create_app(CATALOG_MODE=live, local SQLite/test identity) -> HTTP /api/catalog returns 200 and actual Opus 5, Sonnet 5, GPT-6 Astra IDs. Fixed 2026-09-13 + committed snapshot yields 12 recent records, all in range, all ungranted/non-executable. This is local serving-chain verification using real cached metadata, NOT hosted Cognito, real Lambda or deployment acceptance.

Executed: pytest tests/test_model_discovery.py tests/test_discovery_api_catalog.py tests/test_discovery_catalog_integration.py tests/test_model_recency.py tests/test_production_discovery_handoff.py -q
Result: 60 passed, 2 pre-existing deprecation warnings, 0.43s.

Deployment plan (not executed):
1. Read actual Business/API serving Lambda package/config and exact native source config; keep rollback package and env unchanged backup.
2. Package reviewed code + reviewed_model_discovery_cache.json. Derive workspace scope from current authorized configuration, then apply with_discovery() to that native source config; retain login/auth/grants/Gateway/provider sources and existing execution packages. Do not overwrite current package from an older generic full repository zip without comparing preserved modules/config.
3. Publish only reviewed Business package/config and frontend after owner approval. No auth/authorizer/worker/IAM changes. Verify packaged cache path resolves inside Lambda.
4. Hosted QA /api/catalog must include the three named models and current rolling-window dates; verify existing login, old drafts/IDs and permissions, then actual UI screenshots.

Still pending: actual target package/config reconciliation, hosted verification, publishing approval. No claim of cloud deployment.
