# Generic MCP packages and deployment deletion

Continue the current `feat/runtime-snowflake-mcp` branch and the existing Studio.
Preserve prior deployed resources and immutable release receipts.

## Requested behavior

- New MCP onboarding offers an HTTPS endpoint or a complete ZIP. It has no
  Snowflake fields, profiles, single-file bundle overlay or provider defaults.
- The Snowflake example owns its non-secret account, reader role and warehouse
  in `main.py`. It starts and lists tools without deployment environment variables.
- Authentication follows deployment. User OAuth 3LO, service OAuth 2LO, IAM and
  reusable authentication remain separate choices.
- An optional, provider-neutral package manifest names a read-only bearer
  validation tool. The authenticated bridge calls that tool with the token in
  a private IAM-protected header. The package validates its own provider token;
  the bridge rejects missing/invalid validation results. The validation tool is
  excluded from published business tools. Existing Snowflake bridge bindings
  remain compatible.
- The MCP page lists uploaded deployments, including failures and unfinished
  uploads, and offers name-confirmed deletion. A deployment with a live
  registration or saved-agent dependency cannot be deleted. Registration removal
  continues to use the existing Gateway/Registry retirement workflow.
- Deployment deletion removes the owned Runtime, generated workload identity
  and exact uploaded S3 object versions. Shared roles, credentials and remote
  data remain. Diagnostic logs retain their existing 14-day retention.
  Tombstones and operation receipts remain for audit and idempotency; they do
  not consume deployment slots. No automatic replay after uncertain writes.

## Execution and verification

1. Add failing API, cloud-boundary, real MCP protocol and browser regressions.
2. Implement the generic package contract and example, keeping old bindings
   immutable. Implement dependency-fenced deployment retirement and recovery.
3. Run focused tests, broader affected suites, build, compilation, IaC synthesis,
   policy-size checks and `git diff --check`.
4. Audit the existing AWS target, release only owned Lambda members, the
   compatible bridge update and frontend assets. Wait for successful terminal
   states and verify deployed code hashes and resource tags.
5. Upload the corrected complete package through authenticated Studio. Verify
   package startup and seven business tools. Test that same disposable package's
   delete action and native resource absence. Do not replay
   historical Snowflake queries or silently alter catalog quotas.
6. Save sanitized evidence in
   `artifacts/studio-generic-mcp-package-20261004/`, update the package guide and
   provide the exact tested ZIP with acceptance steps and remaining gates.

## Verification status

- The generic upload form, package-owned Snowflake configuration, separate
  authentication choices and deployment deletion are implemented and published
  to the existing Studio.
- Authenticated Chrome uploaded the complete 30,486,266-byte ZIP, reached READY
  and discovered all seven business tools. Native verification confirmed the
  exact ZIP digest, IAM protection and no provider environment variables.
- The 3LO and 2LO forms were checked separately. A new endpoint-bound reference
  reused the existing 3LO provider. Gateway/Registry registration reached REVIEW.
- Publication correctly stopped at Research's 35-entry catalog limit. The user
  then approved Operations for a temporary agent/query test and cleanup of its
  newly created resources. A fresh upload and publication there succeeded:
  Runtime and Gateway target READY, Registry APPROVED, eight Operations-only
  catalog entries. Existing registrations were preserved.
- The user approved a temporary Operations membership for the existing account.
  Its three groups, 37 grants, 35 Research grant markers and three existing
  agents were preserved before the change. Admin stayed enabled. Normal
  signed-token renewal loaded Operations, and the real UI created and deployed
  a new agent with Knowledge Q&A, Claude Haiku 4.5 and only the new package's
  seven tools. No new account was created.
- The new agent completed Snowflake 3LO and a successful `test_connection`.
  A second fresh request ran the exact read-only `PRODUCTS` count query and
  returned **3**, query ID `01c782eb-0108-cc36-0011-12b2000a916a`.
  Both values were verified against the actual tool span in an immutable S3
  trace, then matched to the UI reply. The connection and query proof files are
  linked from `hosted-operations-e2e.json`.
- The first agent-deletion preview returned HTTP 403 because Studio no longer
  accepted the session. After normal sign-in in the existing QA tab, cleanup
  completed through the UI. Native reads confirm the test agent's Runtime and
  data are absent, the test Gateway/Registry bindings are absent, all 18 package
  resources are removed, and only the temporary OAuth reference was deleted.
  The existing provider and secret remain. No retry or operator recovery was
  needed for this Operations deletion.
- Research and Admin memberships are restored. The original 37 grants, Research
  grant markers, three agents and 30 pre-existing MCP records match the
  preservation snapshots. Only the 19 Operations grant markers created by this
  test were removed, with a fenced transaction and audit event. The real UI is
  back in Research and displays exactly the original three agents.
  Proofs are linked in `hosted-operations-e2e.json`; the restoration journal is
  `existing-account-operations-change-20261005.json`.
- The test registration and authentication reference were deleted through the
  UI, with native absence and preservation of the shared provider/secret checked.
- Live deployment deletion found three gaps: AgentCore also
  requires `DeleteAgentRuntimeEndpoint`, and S3 rejects `If-Match` together with
  `VersionId`. One SQS delivery per resource also reaches Lambda's recursion
  limit. All three have failing-then-passing regression tests and scoped releases.
  The S3 fix preserves the immutable version fence and newer object versions.
  The worker batches confirmed deletion progress with per-step authorization
  and claims, yields for pending resources or time limits, and keeps recursion
  protection enabled.
- Final backend suite: 2,175 passed, four skipped. Snowflake example: 105 passed.
  Affected browser suites: 47 passed. TypeScript/Vite build and diff checks pass.
- The original test's 18 owned resources are absent. After Lambda dropped its
  queued continuation, one operator delivery of the same expired job recorded
  the authority deadline failure; the UI then reconciled and finished deletion.
  The original failure evidence is retained.
- A fresh complete-package upload and deletion passed on the final release:
  all 18 owned resources removed in 61.72 seconds, zero retries and no operator
  recovery. Native absence checks passed; recursion protection stays enabled.
  Browser monitoring through Operations publication recorded zero console/page
  errors. The post-release security/tag audit passed all 89 resource checks.
  Unauthenticated API/MCP access was denied and the web bucket remained private.
- The final audit after Operations cleanup passed all 84 resource checks.
  The successful cleanup recorded no browser console, page, resource or
  unhandled-rejection errors. The temporary browser observer and its exact S3
  asset version were removed; no unrelated frontend asset was changed.

Evidence and the exact tested package/source kit are under
`artifacts/studio-generic-mcp-package-20261004/`.
