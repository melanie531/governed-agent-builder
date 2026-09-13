# Discovery response filter additive review

Base b3c09044921abb55bd048ced58bd4bd4f498a74d. No merge of 74cd6ad, no IAM changes or deployment.

GET /api/catalog filters model rows using existing backend recency classification, after computing the existing evidence-backed identity access summary. Raw catalog records, per-ID details, pinned versions and saved-draft readers are unchanged. /api/capabilities previously called ai_catalog directly; it now independently uses the SAME authorized projection so list filtering does not remove historical rows from its compatibility consumers. This does not bypass visibility or grants.

RED: mixed provider returned old Haiku route in default catalog. GREEN: test_catalog_response_compatibility exercises default/kind/q list exclusion; recent Opus and unrelated tool retained; old original-ID detail and version both 200; persisted old draft GET 200 retains original model ID and pinned version; capabilities still includes old route; provider records unchanged; other workspace receives 404 for detail and draft and cannot see old capabilities. This uses synthetic transport and SQLite persisted draft, NOT real deployed user evidence. Missing second business identity live isolation remains NOT_RUN.

Existing synthetic discovery API fixtures now supply explicit synthetic launch evidence, rather than expecting unclassified synthetic IDs to pass a production recent-only filter. No fabricated official data in product.

Executed /tmp/gab/.venv/bin/python -m pytest tests/test_catalog_response_compatibility.py tests/test_model_access_scope.py tests/test_access_summary.py tests/test_runtime_discovery_coexistence.py tests/test_discovery_api_catalog.py tests/test_live_catalog.py tests/test_production_discovery_handoff.py -q
Result:36 passed,2 existing warnings,1.31s. Includes real cached inventory via local production factory test; no inference. No claim Haiku is business-callable. UI untouched relative to b3c0904; UI mock/navigation results from that version are historical, not a new hosted acceptance.
