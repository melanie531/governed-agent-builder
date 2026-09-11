# Native catalog bounded SOURCE fix report

Review base: `a4f4659a4f8616acfa6d6464e1c3ec682d308149`.
Branch: `feat/live-capabilities`. Source only, NOT DEPLOYED; no cloud reads,
IAM/permissions, infrastructure, runtime environment, website/login/data changes.
No private skills, SSM or secrets read. All tests use synthetic records/sessions.
No nested worker or CC. Concurrent telemetry/model-gate work is not staged here.

## Implemented
- F1: validate all source/exposure inputs before SDK construction; native workspaces
  must be explicit valid nonempty lists. Projection fails closed on invalid scopes.
- F2: Search maxResults=20, query length 1..256, local UI search unchanged.
- F3: validate supplied versions (including markdown-only SKILL), structured JSON,
  minimal native protocol shapes, contradictory versions and callable schemas.
  Unknown/malformed entries cannot be requested. Allowed SKILL extensions remain
  private. CUSTOM named MCP remains CUSTOM, zero tools.
- F4: versioned deterministic approval digest covers full record identity/type/
  version/name/displayName/description/descriptors. Mismatch hides records/children;
  each child must pin the identical parent revision and intersect workspace scope.
- F5: target digest includes exact Gateway ARN, target ID/name, inference and
  allowlisted NONSECRET auth metadata. Approval additionally pins exact qualified
  model ID and entire enumerated model projection. Returned full identities checked.
- F6: malformed/missing data, error envelope, continuation, duplicate/malformed
  model IDs or missing ownership fail closed, including clearing cached snapshots.
  Actual data=[] is valid empty discovery, never execution acceptance.
- F7: owner-approved v2 account/region/full-ARN configuration; one shared SDK
  session, STS account check before source reads, region/full-resource equality.
  Tests inject the STS/session seam and cannot use factory injection to skip it.

## Migration and approval gate
See docs/NATIVE-CATALOG.md for exact canonicalization and explicit v1→v2 migration.
Old digests/config cannot silently authorize v2 content; no default old registry IDs.
Redacted schemas are DISPLAY ONLY, not executable validation schemas.
InvokeGateway remains broader invocation permission, not IAM-enforced GET-only.
No added grant or bypass of ENFORCE/Denies. All native execution remains unverified.
Owner must next approve exact current Studio source config (account/region/full ARNs,
record hashes/workspaces and enumerated routes), then separately authorize connection
and IAM/transport acceptance. Prefer registry-only while model listing is unresolved.

## Verification
Focused command:
`.venv/bin/python -m pytest tests/test_native_catalog.py tests/test_native_catalog_fixes.py tests/test_live_catalog.py tests/test_authorization.py tests/test_hosted_auth.py tests/test_callback_recovery.py tests/test_hosted_callback.py tests/test_email_verification.py -q`

Includes independently written review counterexamples and authorization reapplication
across user/workspace changes on cached snapshots: counts/search/detail/version/request.
No UI changes; no new UI build claimed. Existing Starlette/Mangum deprecation warnings.
Earlier test runs exposed test-harness syntax/SDK-null/app-mode setup errors, corrected
before final verification. No authenticated denial occurred; no alternate environment.

Well-Architected: Security hardened offline, real source/IAM approval pending;
Reliability fail-closed and bounded, upstream indexing remains eventual; Performance
and Cost bounded metadata fanout but not measured live; Operational excellence explicit
migration/receipts; Sustainability cache reuse unchanged, no infrastructure added.

Final focused result: **201 passed**, 3 existing deprecation warnings, 10.08 seconds.
`git diff --check` passed. No UI files changed.

Exact tested source commit: `848c0f4dd33b483a265123fb762562f9bdcf3af3`.
The following report-only commit adds this receipt; implementation/tests are unchanged.
`origin/main` is not present in this checkout; outgoing commits were inspected against
the existing tracking branch `origin/feat/live-capabilities` instead.
