# Second-batch review corrections (not deployed)

On top of 28c609c. IAM is not changed by this patch.

- Default Models UI filters mixed API results to backend-classified recent models (local fixture mode kept for local harness). Old Runtime Haiku remains in the API/lookup; no binding deletion or backend lookup filtering. Mixed real-shaped frontend test excludes legacy Haiku and old discovery while backend coexistence test preserves old lookup.
- Model-only summary; identity = exact model_id, falling back to record_id/id. All rows for the same model deduplicate. Current caller granted must be explicitly true. Requestable never increments granted. Callable requires granted AND usable AND execution_ready on the SAME route; unsupported/fixture entries cannot be callable. Binding verified alone cannot override policy/usability.
- UI shows granted/total for the same eligible model list; no MCP/skill rows in counts. Discovery-only rows say Not configured, not blocked; no permissions changed.
- Pending status link invokes React navigation callback wired in main.tsx, not an unhandled hash. Browser test actually clicks the link, reaches Requests, and sees the original purpose and Pending approval record. Mock capabilities endpoint is included because Requests loads it before histories.
- 409 pending is distinguished from unrelated 409 conflicts. Error text stays in the open modal and purpose is retained.

Executed at candidate tree:
- npm run build --prefix frontend: exit 0 (existing bundle-size warning).
- Isolated Playwright run on port 5297: models-access + request-access-feedback, 12 passed (12.3s). Covers mixed source default, zero/positive/deduplicated grants, success201, duplicate409+real React navigation, unrelated409,403,500,503. These are mock HTTP browser tests, not live deployment verification.
- pytest model_access_scope/access_summary/runtime_discovery_coexistence/discovery_api_catalog/general_requests_iam/general_requests: 35 passed, 2 pre-existing warnings (2.07s).

Not a full-suite PASS. UI not deployed. Recent live IAM application fixed general_requests GET500 separately; same batch UI approval remains pending. Old draft live read and second-business identity nonempty isolation remain separate live acceptance obligations; retained source/mock evidence is not a substitute.
