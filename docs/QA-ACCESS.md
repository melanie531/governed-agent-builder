# Dedicated administrator-enrolled Studio QA access

This is access to the existing Agent Studio, not AWS console/IAM account creation.
It does not enable model/tool integration or change any fixture/evidence labels.

## Authority and policy

Owner approval: direct creation of dedicated business and admin QA accounts without
email enrollment, 2026-09-12. The email-username Cognito pool requires the reserved
identifiers `qa-business@example.com` and `qa-admin@example.com`. These are not
verified email addresses. Provisioning uses AdminCreateUser with SUPPRESS and a
cryptographically random password, followed by AdminSetUserPassword Permanent.
The normal first-password prompt is intentionally completed by the trusted
administrator for these dedicated QA identities only. No prior user's password,
email_verified attribute, pool, app client, infrastructure or IAM policy changes.

The backend-only `qa_enrollments.json` is protected Lambda deployment state, not
frontend content, a token claim, a header, app setting or editable admin API. It
binds each exact Cognito subject to issuer, client, Studio origin, a single approved
Cognito group, role, workspace, administrator ARN, approval reference, enrollment
time and 30-day expiry. The actual record lives in restricted local release
artifacts and deployed Lambda packages, not Git. Code deployment permission is the
trust boundary; no runtime IAM expansion is needed. The backend checks it before
session creation. Invalid/ambiguous entries fail closed. Every authenticated
request rechecks enrolled subjects; QA cookie identifiers have an authenticated
server-record-bound prefix so removing a record still denies existing QA sessions.
The prefix is not an auth credential or bypass: changing it breaks the stored hash.

JWT signature, issuer, audience/client, purpose, nonce, PKCE, expiry, subject and
single-group checks remain required. Email validation remains unchanged; ordinary
unverified users remain pending without principal/grants/business access. No domain
exemption, public signup, persona picker, owner password reuse or email override.

## Secret custody, rotation and revocation

Only these exact target-account SSM SecureString paths are used:

- `/governed-agent-builder/qa/business/username`
- `/governed-agent-builder/qa/business/password`
- `/governed-agent-builder/qa/admin/username`
- `/governed-agent-builder/qa/admin/password`

Credentials are generated in process and passed directly to the existing AWS SDK
provider and SSM. They never enter source, logs, CLI arguments, environment files,
chat or model context. Exact-name metadata existence checks precede creation; the
one-shot script refuses existing users/parameters instead of resetting/adopting
them. Cognito/SSM admin calls are audited by CloudTrail. The local receipt contains
only state and paths. Existing authorized peer roles may use their own approved SDK
access; no new read grants are created.

Rotate the dedicated passwords and update their exact SSM parameters before the
30-day enrollment expiry using an authorized secret-handling process; re-enrollment
requires owner/admin review. To revoke application access, disable the enrollment
and release all three authentication-bearing packages (Auth, Authorizer, Business),
or remove the enrollment file from all three; marked QA sessions then fail closed.
Also disable the Cognito QA user and globally sign it out. Disabling only Cognito
does not revoke already-issued JWTs immediately. Do not restore an old enabled
registry when rolling back a revoked subject. Full package rollback to the original
non-QA versions requires purging QA hosted_sessions first because the old code does
not implement enrollment rechecks. Do not touch other users' sessions.

## Release and verification scope

`qa_access_provision.py` checks STS, existing CFN outputs and the exact CloudFront
origin, creates only two absent QA users, assigns exactly one group each, verifies
CONFIRMED/enabled and email_verified false, and stores non-secret enrollment state.
`qa_access_release.py` requires committed code/tests, checks fresh function revisions,
patches only hosted_auth.py plus the QA module/registry in each existing package,
preserves all other ZIP bytes, updates only three functions' code and independently
downloads/readbacks hashes. It never replaces a pool/site/stack or changes Lambda
configuration. Rollback ZIPs and hashes are captured before writes. Existing auth
source differences across packages are preserved rather than publishing unrelated
source changes.

**Code tests:** exact subject business/admin admission with unverified email;
ordinary/wildcard/other-subject denial; forged signature and group escalation;
issuer/client/origin/role/workspace binding; expiry and disable/delete revocation;
cookie manipulation; existing ordinary verified/unverified auth regression.

**Cloud/API:** record account status and exact SSM paths, matching deployed code
hashes, unchanged physical resource IDs and pool policy, and unauthenticated 401s.
These checks are not a real signed-in QA role-isolation acceptance test.

**Browser:** only report observations actually made. This tool session has no
host-owned protected SSM-to-browser credential binding. Do not put credentials into
browser tool arguments, fetch them into model context, inject sessions, or mark
login success based on source/offline tests. Authenticated business/admin UI
inspection requires an authorized secure UI-entry method or human entry directly
into Cognito; do not ask for passwords in chat.

## Security-first / Well-Architected review

- Security: exact signed identity plus deployment-owned enrollment; no IAM widening,
  no fabricated verification; HTTPS and existing secure cookies retained. Deployment
  writers remain trusted administrators. Browser acceptance is explicitly pending.
- Reliability: fail-closed expiry/revocation and fresh revision fencing; per-function
  rollback artifacts. Partial three-function releases can temporarily deny QA and
  must be reconciled from receipts, not treated as complete.
- Operations: reproducible tests, code commit, CloudTrail admin actions and downloaded
  package proof; rotation/revocation documented.
- Performance: local bounded two-entry lookup per request, no new network lookup.
- Cost: same serverless resources plus four standard SecureString parameters.
- Sustainability: no new always-on resource or additional deployment stack.
