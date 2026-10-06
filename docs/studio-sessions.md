# Studio sessions

A successful Studio sign-in creates a server-side session lasting up to 24 hours
from Cognito's original token issue time. Its opaque browser cookie is Secure,
HttpOnly and SameSite=Strict. Tokens remain on the server.

Cognito access and ID tokens still expire after 15 minutes. Studio renews them
when an authenticated request or queued job needs a token with fewer than five
minutes remaining. Renewal verifies the signed access and ID tokens, client,
issuer, subject, approved membership, and verified email or current QA enrollment.
The cookie, CSRF token, selected role and original session deadline remain stable.
Per-user tool invocation also requires at least three minutes of actual access
token validity.

The existing Cognito client already permits one-day refresh tokens with revocation
enabled and rotation disabled. This release changes the backend; it does not
increase access-token lifetimes or modify the identity stack. Older sessions
without a retained refresh token keep their previous shorter lifetime. One fresh
sign-in is required to start a renewable session.

Sign-out removes the server-side session. A token exchange already in flight cannot
recreate it. Invalid/revoked renewal credentials require sign-in; temporary provider
failures preserve the session for a later request. Concurrent successful exchanges
use the first persisted result, and storage conflicts do not replay the provider
request. Background authorization still checks current grants and job ownership.

The 24-hour limit is absolute, including idle time, and is never extended by
activity. Cognito's login-page cookie and Snowflake/AgentCore consent links have
separate lifetimes. The latter still require completion within ten minutes.

## Validation and deployment

Evidence lives under `artifacts/studio-session-24h-20261003/`. Signed-token HTTP
contracts exercise both SQLite and DynamoDB repositories, including idle renewal,
expiry, logout races, concurrent renewal, membership rejection, email verification
and queued-job authority. These tests use an offline provider endpoint; hosted
renewal needs separate authenticated evidence.

`scripts/scoped_session_release.py` applies reviewed ZIP-member patches to the
existing Auth, Authorizer, Business and Worker Lambdas. It verifies the retained
predecessor packages and target account, journals writes, uses versioned encrypted
artifacts, and checks revision IDs, code digests and unchanged configuration.
The full security audit accepts only a verified scoped receipt through
`--scoped-session-renewal-receipt`.

Do not rerun an uncertain code update. Read back the exact function revision and
digest, then reconcile the retained operation. The release retains the original
ZIP for every function. Rollback is a separately journaled code update to those
exact original bytes, guarded by the current revision. Reverting the session
implementation ends renewal for new and existing sessions; users must sign in
again. No CloudFormation, Cognito, IAM or frontend rollback is needed for this
backend-only release.

## Hosted acceptance journey

1. Sign in in the normal Chrome profile. Studio opens the authenticated workspace
   and its session has approximately 24 hours remaining.
2. Open the existing Snowflake agent and run bounded schema/table discovery.
   Complete separate provider consent if requested. Results must have successful
   tool spans and immutable execution receipts.
3. Leave the session open across the original access-token expiry, then access
   the authenticated workspace again. Studio remains signed in, with the same
   session and deadline and a newly issued access token.
4. Sign out when finished. Authenticated routes reject the former session.

## Release continuation — 2026-10-03

The retained scoped release now verifies all four Lambda updates: Worker,
Business, Authorizer and Auth. The interrupted Authorizer update was reconciled
from its live code digest without repeating the write. The remaining Auth update
then completed successfully. AWS's automatic Python runtime patch is recorded
explicitly; all other configuration fingerprints still match the reviewed bases.
Every changed ZIP member matches the current source.

The retained backend suite passed 2,038 tests with four skips, and the release
contract checks passed 41 tests. These suites were not rerun during continuation;
`git diff --check` passed again.
The post-release security and retention-tag audit passed all 65 resource checks
with no failures; its full report is retained as `security-after.json`.

Hosted acceptance remains blocked. The native browser workflow could not start:
`Unable to determine Orca.app path from symlink: /usr/local/bin/orca`.
No fresh sign-in, live token renewal, or further Snowflake schema/table discovery
is claimed. The remaining acceptance journey above must run in the existing
normal Chrome profile once browser control is restored. Sanitized continuation
status and source/package comparisons are retained in the same evidence directory.
