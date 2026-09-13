# Actual deployed Runtime-source compatibility

Compared against downloaded Business ZIP SHA256 717cac2f89db20042fa291c561dca3d8f0b01bfaf1e8a8a70f0218d8491b44f4. deployed-live-catalog.patch is the complete candidate diff against the ACTUAL deployed live_catalog.py, not only a git baseline diff.

Preserves runtime_model_routes validation and RuntimeModelCatalog construction alongside discovery_sources. Existing runtime_model_catalog.py is copied byte-for-byte from deployed ZIP. Original config keys and nested Runtime bindings are untouched by with_discovery overlay. No cloud writes.

Read-only actual-source probe with deployed botocore models: old Runtime provider returns 1 record; discovery provider returns 12. Full catalog read blocked from EC2 by Registry EndpointConnectionError; not evidence that hosted Registry is down.

Local factory compatibility proof: runtime_model_routes validates and constructs RuntimeModelCatalog; its synthetic old-route response remains byte-equivalent after combining. Actual committed discovery cache yields 12 recent rows including Opus 5, Sonnet 5, GPT-6 Astra at 2026-09-13. Old Haiku route remains resolvable in combined records, excluded from new discovery subset. No automatic execution/authorization. Runtime transport and validate_source are mocked in this local coexistence test, explicitly not live governance validation. The actual-source probe above uses real validation/readback.

Command: /tmp/gab/.venv/bin/python -m pytest tests/test_runtime_discovery_coexistence.py tests/test_production_discovery_handoff.py tests/test_discovery_api_catalog.py tests/test_model_recency.py tests/test_general_requests.py -q
Result: 40 passed, 2 existing warnings (1.81s).

Existing saved-draft LIVE lookup has not been re-exercised for this revision; existing route identifiers are retained, historical-binding test exists, neither substitutes for hosted saved-draft acceptance. No deployment has occurred. Review the deployed-source diff before approved incremental packaging.
