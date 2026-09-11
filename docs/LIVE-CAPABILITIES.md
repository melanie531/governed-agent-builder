# AI Catalog source milestone

**Not deployed. Live untested. Existing Studio, identity pool, data and deployed versions unchanged.**

This increment adds the business-user AI Catalog and strengthens catalog access requests. It does not deliver the real end-to-end AgentCore journey yet. Current deployment remains fixture execution. Human login is already confirmed by the owner; login is not the remaining integration blocker.

## Implemented

- Cloudscape AI Catalog: Models, MCP servers, Tools, Skills; Agents only when live A2A record listing is available. Foundations remain separate.
- Server-side available/requestable/blocked semantics, safe metadata projection, workspace visibility before search/count/detail/version results, and hidden-ID request denial. Foundation compatibility lists also omit forbidden IDs.
- Durable user/workspace/purpose-scoped requests using the existing SQLite/DynamoDB repository; version pinning, bounded submissions, separate admin approval/rejection with substantive reason, audited decisions. Grant does not onboard a connector. Live grants additionally bind workspace. Existing revocation checks still block fixture versions and jobs.
- Injectable live SDK metadata adapters for Model Gateway and AWS Registry. Explicit Bedrock inference target model mappings only, Claude/OpenAI only; no guessed connector routes, Nova, first-party OpenAI or external Gemini. Approved safe metadata must be supplied by an operator exposure policy. Live provider failures never fall back to fixtures.
- Offline-tested Runtime create/update/get contract: exact-target approval gate, role/update allowlists, versioned S3 reference, immutable numeric version and definition/manifest bindings, nonblocking readiness return. Full-definition manifest and fail-closed evidence/budget validation helpers.
- Offline-default `scripts/live_preflight.py`; optional explicitly selected read-only existing-resource checks, not executed during this milestone.

## Not wired or accepted

- No production SDK provider factory/configuration or cached live catalog refresh. `CATALOG_MODE=live` without an injected provider returns 503, while session/login remains functional. Existing default `fixture` catalog stays explicitly labeled. Do not enable live mode on the existing deployment yet.
- No executable Strands/AgentCore harness, real Model Gateway/MCP transport, artifact build/upload, live source export, actual Runtime deploy/invoke, SQS live lifecycle/continuation integration, real trace retrieval or authenticated managed judge evaluation. No bounded paid acceptance runner yet; the budget helper is not runtime metering.
- All live records intentionally have `integration_ready=False`; even approved grants cannot imply execution readiness. Live-mode composition and fixture execution are blocked.
- No real account/resource discovery, AWS writes, paid calls, new pools/sites/accounts, reset, PR or merge.
- The prior peer review of 9f1d76f does not cover this increment. Parent must request independent review of the new published commit and obtain exact account/resource authority before any existing-stack update. Use state-bound changeset/update handling, not a fresh-account deployment script.

## Verification

Baseline: **212 backend tests passed** at eef8f2a74919f6ecd4221c8a71d34568882a1b17.
Final backend: **248 passed**, 3 pre-existing dependency deprecation warnings.
Frontend: TypeScript + Vite build passed; existing large-chunk warning remains.
Offline preflight: SDK shapes verified; resource_reads=false; execution_ready=false; live acceptance NOT RUN.
**Browser: 8 passed (17.5 seconds), including both preserved journeys and new catalog approval/fail-closed cases.** All browser adapters, JWT and Moto evidence are explicitly synthetic/offline, not AWS acceptance.

One intermediate full backend run observed the existing `test_dynamo_verification_atomic_budgets_and_consume` concurrency test return 7 reservations instead of 5 under Moto. The final full rerun passed without authentication/verification-store changes. The intermittent behavior remains a review item, not declared fixed. An existing external-policy request test was updated to the stricter required 404/non-discoverability behavior; rejection reason and forbidden-grant coverage remain.

## First accepted live slice still required

Catalog → scoped request → separate approval → refreshed compatible selection → frozen user instructions + one Claude/Bedrock model + one read-only MCP tool + one skill → actual immutable Runtime deployment → real Tool Gateway and Model Gateway calls → dataset/custom-rubric evaluation with authenticated trace/judge evidence → UI. Revoke and stale-version negative cases must pass as well. No missing evidence may become PASS.

See [the source task paper](../work/LIVE-INTEGRATION-PLAN.md) for the exact acceptance sequence, SDK references, security-first/Well-Architected review and remaining limits. Dependencies remain pinned by existing uv.lock and package-lock.json; boto3/botocore contract inspected at 1.43.92.
