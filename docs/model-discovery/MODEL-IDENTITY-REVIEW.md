# Evidence-backed model identity counting — not deployed

Addendum to 54fd310. Read-only live bedrock:GetInferenceProfile for exact us.anthropic.claude-haiku-4-5-20251001-v1:0 returned the same inferenceProfileId and models[].modelArn with one unique foundation-model identity anthropic.claude-haiku-4-5-20251001-v1:0. Sanitized proof in backend/model_identity_evidence.json. No prefix stripping, vendor/name guessing or inferred aliases. Other profiles remain unmatched unless separately evidenced. Evidence is a reviewed snapshot, not continuous live revalidation.

The summary first defines eligible models from recent discovery/local fixtures, then joins all visible caller model projections by exact model ID or the one evidenced profile mapping. A historical route cannot introduce an out-of-window model into the list; it can contribute a grant to an already eligible same model. Original record IDs, per-component grants and lookups are not rewritten. No MCP/skill summaries.

Callable requires granted AND usable AND execution_ready on the SAME valid row (nonfixture, not explicitly unsupported). Tests split those facts across an evidenced discovery/profile pair and assert callable=0. Unproven eu. alias does not transfer grant. Test data marks Haiku recent solely to exercise identity association; it is NOT a claim Haiku is currently in the actual six-month window.

UI prefers backend access_summary for evidenced associations, with explicit-field count compatibility when absent; no fabricated access. The frontend summary-consumption test uses synthetic server counts to test rendering, not to establish model identity or permissions. Backend tests establish identity join behavior.

Validation: backend model_access_scope/access_summary/runtime_discovery_coexistence/discovery_api_catalog: 18 passed, 2 existing warnings, 0.65s. npm build exit0. Models-access/request-access-feedback Playwright:13 passed,12.9s. Mixed old/new list and real React status-navigation test retained. Not full suite, not hosted UI acceptance. No IAM writes or model calls; not deployed.
