# Snowflake guide end-to-end acceptance

## Goal and authorization

Validate the generic instructions with an independent agent acting as an end
user in the default Chrome profile. The user authorized new Snowflake setup,
MCP deployment and connection, agent creation as a Business user, and real UI
tool tests in the burner account. Keep the successful resources for review.
Only failed artifacts created by this run may be removed.

The independent tester followed the Snowflake OAuth guide, `docs/start-here.md`
and the linked package README. Their Snowflake instructions are now consolidated
in [the Snowflake setup guide](../snowflake-setup.md). The coordinating agent owns
diagnosis and corrections. Neither agent may substitute backend creation or
invocation for a documented portal action.

## Target and ownership

- Existing branch: `feat/runtime-snowflake-mcp`; preserve unrelated dirty work.
- Burner profile: `burnner`, account `649396817992`, region `us-west-2`.
- Studio: `https://d1jd4cmsbexr67.cloudfront.net`.
- Browser: the user's existing default Chrome profile, including Snowflake.
- Run prefix: `guide-e2e-20261006a`; confirm names are unused before creation.
- Evidence: `artifacts/snowflake-guide-e2e-20261006/`.
- Tester owns `qa/` evidence and `package/` build inputs under that directory.
- Coordinator owns this plan, the resource journal, documentation and any
  narrowly scoped product fixes.
- Tester has exclusive control of the test browser tabs while active. The
  coordinator uses local checks and read-only cloud diagnostics in parallel.

Do not modify existing Snowflake integrations. New grants must be limited to
the new test role and intended user. Use a small synthetic dataset or approved
read-only existing data; never retain raw customer data or credentials in
evidence. Journal created object identifiers before cleanup.

## Tasks and acceptance

| Task | Owner | Depends on | Acceptance |
| --- | --- | --- | --- |
| T1. Establish browser, target, ownership and security baseline | Coordinator and tester | None | Current burner identity verified, default-profile tabs identified, baseline inventory and audit recorded |
| T2. Follow Snowflake setup and prepare the package | Tester | T1 | New integration, role/access, copied OAuth fields and complete package obtained from the guide without undocumented assistance |
| T3. Deploy, configure authentication and publish MCP | Tester | T2 | ZIP READY, actual callback registered on the new integration, OAuth saved, tools reviewed and published through Studio |
| T4. Create an agent in Business view and call tools | Tester | T3 | New agent deployed; consent and connection, table listing and read-only query succeed in Chat with tool evidence |
| T5. Fix failures and independently retest | Coordinator, then tester | Any failed task | Reproduction recorded; affected documentation or code corrected; same UI step and downstream flow pass |
| T6. Retain success and hand off | Coordinator and tester | T4 and T5 | Reload persistence, browser diagnostics and security/tags checked; successful resource names and exact user verification steps supplied |

## Failure and recovery loop

1. Tester records the document section, visible action, expected and actual
   result, sanitized error/request identifier and any newly created resource.
2. Tester reports the failure to the coordinator and does not invent a backend
   workaround or repeat an uncertain mutation.
3. Coordinator reproduces the relevant defect, makes the smallest correction,
   and runs focused and affected broader checks. Documentation stays generic.
4. If hosted behavior changes, release only the affected burner preview
   artifacts after security checks; verify successful deployment and tags.
5. Tester rereads the revised instructions and repeats the failed UI step.
6. Delete a failed run-owned resource only when replacement is necessary and
   ownership is recorded. Retain successful resources and all sanitized failure
   evidence.

## Completion boundary

Infrastructure readiness, saved OAuth, consent, or local tests alone do not
complete the goal. Completion requires the independent agent's full portal
journey and actual Snowflake tool results from the new agent, with the final
successful setup left available for the user.

## Progress

- T1 complete: burner STS identity matches the target. The independent tester
  confirmed authenticated Platform Admin and Snowflake ACCOUNTADMIN views in
  the existing Chrome profile and successfully read both pages through the UI.
  Evidence: `qa/t1-preflight.json`.
- Native Orca helper unavailable:
  `Unable to determine Orca.app path from symlink: /usr/local/bin/orca`.
  The existing Chrome application responds to AppleScript/JXA; no separate
  browser profile was launched.
- Initial tester Wegener (`01a11075-e4b1-76b3-9b0b-baea5dda2653`) did not
  return a checkpoint or write evidence and was stopped. No test-created
  resources were reported by that session.
- Independent tester: Bohr (`01a1107f-83dd-7b10-bca9-925270553b43`).
  First assignment is bounded to browser/session preflight; subsequent tasks
  will use the same tester after each concrete checkpoint.
- Read-only pre-test audit passed all 64 resource checks with zero failures.
  Evidence: `artifacts/snowflake-guide-e2e-20261006/security-before.json`.
  The two historical DELETED package receipts are not active failures.
- Exact initial guide hashes are in `document-baseline.json`. The tester has
  been cleared to proceed after confirming the authenticated UI sessions.
- T2 complete except secret retrieval, deliberately deferred to form entry in
  T3. The new reader role returned two sample rows with secondary roles NONE.
  The new OAuth integration is disabled and its five nonsecret form values
  were retrieved. Existing warehouse COMPUTE_WH was reused. Package checks:
  108 tests passed; ZIP 30,486,262 bytes, expanded 68,693,379 bytes, ARM64
  native libraries and root layout verified. Evidence: `qa/t2-checkpoint.json`.
- T5/F1: the tester initially ran dependency sync at the repository root.
  All 17 removed packages were restored at their exact versions and independently
  verified. The package README and Snowflake guide now explicitly name the
  required working directory. Evidence: `f1-package-working-directory.json`;
  tester acknowledgement of the clarified instructions passed in T3A.
- T3A complete: native Chrome file chooser selected the ZIP; UI status advanced
  through upload, packaging, deployment and discovery to READY. The tester
  clicked Use this MCP package. Deployment ID:
  `71ccd072db334d68a343e53f9edb6aab`. Native readback confirmed the same burner
  runtime is READY and tagged `auto-delete=no`; no backend tool invocation was
  used. Evidence: `qa/t3a-checkpoint.json`, `runtime-after-upload.json`.
- T3B complete: the actual callback displayed by Studio was registered on the
  new Snowflake integration. All seven configuration comparisons passed.
  The tester reviewed and published seven MCP tools to Research through the
  portal. Native readback independently confirmed the new OAuth provider's
  account, callback, client ID and retention tag. Evidence:
  `qa/t3b-checkpoint.json`, `oauth-provider-readback.json`.
- T4 in progress: the tester created and deployed
  `Guide Snowflake Reader 20261006A` v1 in Business / Research, using the new
  MCP and the permitted `test_connection`, `list_tables` and `query` tools.
  The first request reached provider authorization. After the user unlocked
  the desktop, the existing request opened Snowflake sign-in in the default
  Chrome profile. No tool result is claimed yet. Evidence:
  `qa/t4-checkpoint.json`.
- T6 in progress: a post-provisioning audit encountered a concurrent governance
  revision while reading application records and did not finish. Its read-only
  transaction contained a revision condition check, with no record changes.
  A later audit after provisioning passed all 71 resource checks, including the
  newly deployed agent runtime, endpoint, identity and logs. No checks failed.
  The two listed DELETED packages are historical deletion receipts.
  Evidence: `security-after-provisioning.json`; the unsuccessful earlier attempt
  remains in `security-after-provisioning-attempt-1.json`.
- Completion audit remains open: provider sign-in, three actual tool results,
  reload persistence and native Console inspection are unproved. The independent
  tester's latest read-only browser check still shows the Snowflake login form.
  Evidence: `acceptance-matrix.json`.
- Human sign-in remains required across three consecutive goal turns.
  At 2026-10-06 10:22:01 UTC, the independent tester confirmed the same Snowflake
  login form and Studio's pending authorization state without interacting with
  either tab. The goal is blocked until the user completes sign-in; all created
  resources and the existing request are preserved.
  Evidence: `qa/t4-signin-blocked-audit.json`.
- T4 resumed after the user reported an AgentCore callback validation error:
  `Value at 'cookie' ... Member must not be null`. The tester confirmed the
  error on the native callback page and Studio's **Sign-in expired** message.
  Exact readback showed the original request expired at 10:13:30 UTC and
  completed zero tools; it was over eight hours expired when inspected.
- T5/F2: both generic guides now cover expired sign-in and missing-cookie
  recovery. The tester independently followed the new instructions, entered
  the same controlled question once in the existing agent and opened a fresh
  native authorization link in the default Chrome profile. The fresh flow
  `218c1b847b2d442999ec34e973834319` is waiting for human Snowflake sign-in in
  tab `1218034059`. No server, integration, connection or agent was recreated.
  Whether a fresh completed flow resolves the callback error remains unproved.
  Evidence: `f2-expired-provider-signin.json`,
  `qa/t4-fresh-signin-human-required.json`.
- T4 complete: the fresh callback returned **Authorization complete**, and the
  pending connection test continued successfully. The tester then submitted
  separate table-listing and count-query messages through Chat. Each produced
  exactly one successful Gateway call. The query returned `ROW_COUNT = 2` and
  Snowflake query ID `01c78e12-0108-d59c-0011-12b2000ce98a`.
- T5/F2 recovery complete: fresh authorization succeeded with the same
  integration, provider, MCP and agent. The original request was expired; no
  authentication or runtime code change was required. The precise browser
  cookie-loss mechanism was not inspected.
- T6 complete: the coordinator verified a new document after reload and the
  persisted query answer, query ID and execution trace. The actual native
  Console showed no errors or warnings. One informational form-autofill advisory
  was recorded. The repeat security audit passed 71 checks. All successful
  resources remain; no cloud or Snowflake resources were deleted.
  Final evidence and numbered user instructions:
  `artifacts/snowflake-guide-e2e-20261006/RESULTS.md` and
  `artifacts/snowflake-guide-e2e-20261006/USER-VERIFICATION.md`.
