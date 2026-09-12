# Builder / Catalog integration

Baseline verified: feat/live-capabilities at 4bc8f3f. Source only; no AWS writes or deployment.

## Trace and bounded changes
- backend/app.py build-options erased approved Foundation Library entries in live mode. Keep library as authority independently of native discovery availability; return actionable NotConnected and no seeded component fallback.
- Add backend/builder_catalog.py: interpret optional platform-owned `native_bindings` manifest metadata (`approved: true`, `components: [{id, version, source_revision, compatible_model_ids?}]`). Exact IDs/revisions only; never alias conversion. Bindings are composition approval, NOT executable adapters.
- Use the same catalog projection/grants/readiness in build-options and AI Catalog. Redact hidden IDs from public foundation manifests.
- Separate live draft validation from execution: permit visible unresolved selections and empty model; preserve prior selections when revoked. Report typed issues on save/read. Reject new hidden/unknown selections. All native execution remains blocked before any job/Runtime creation.
- Existing fixture definitions, history and local simulation stay intact. Never silently substitute fixture execution in live catalog mode.
- frontend main.tsx/AICatalog.tsx: preserve draft across navigation, hand off exact catalog ID/version, retain invalid selections, save live drafts without implicit deployment, display binding/readiness state.
- Tests: approved library survives disconnected/live discovery; exact descriptor projection and shared grants; missing/incompatible/revoked bindings; no Runtime/job on draft save; existing fixture journey; UI build and local journey where feasible.

## Boundaries
No NativeHarness, new roles endpoint, framework, cloud configuration, or production acceptance claim. Runtime execution adapter/evaluation acceptance remains incomplete. Concurrent telemetry and model_gate_faults files are excluded from staging.

## Verification
- Focused backend regression: 176 passed (Builder/Catalog/native/authorization/workflow).
- TypeScript and Vite production build passed; pre-existing >500 kB bundle warning remains.
- Browser: 5 passed, including unchanged full fixture create/evaluate/revise/retest/export/approval journey and a new offline-native Catalog-to-Builder prompt/evaluation preservation case.
- Screenshot: artifacts/builder-catalog-draft-preserved.png (offline browser metadata, not live AWS acceptance).
- Initial broader pytest run: 984 passed, one setup error because concurrent Vite build temporarily removed dist/assets. Full suite rerun after build completed; excluded concurrent telemetry/model_gate_faults test files.
- Persona switch clears in-memory draft to prevent cross-workspace draft leakage; same-persona Catalog navigation preserves it.
- Foundation compatibility is explicit approval metadata only. No live native bindings have been configured by this patch. Runtime remains NotConnected and deployable=false.
- Security: visibility projection/shared scoped grants, hidden manifest bindings omitted, no Runtime creation on save. Reliability: stored pins retained after revocation; stale revisions flagged. Performance/cost: no extra providers or cloud calls beyond existing discovery; bounded patch. Operations: typed readiness issues and local tests. Sustainability: reuses existing library, Wizard, catalog and store; no new service.
- Rerun result: 985 passed, 3 warnings. IMPORTANT: this ran in the shared dirty worktree with the other worker's telemetry.py patch present, and excluded test_telemetry_flush.py/test_model_gate_faults.py. It is NOT evidence that this Builder-only commit fixes OTel flushing or that the clean fixed SHA has a fully green suite. Peer reported an actual OTel flush bug at its earlier fixed SHA; no telemetry files are part of this change.
