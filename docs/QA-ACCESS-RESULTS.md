# QA access release receipt · 2026-09-12

Studio: https://de32ssfw7gsad.cloudfront.net/

Source release commit: `3d6a96981e091e380d3a0e825a4e31822c75f837`, branch
`feat/live-capabilities`. Initial implementation: `a0c5498`.

## Accounts: created and enabled

Both dedicated business/admin Cognito users are CONFIRMED, enabled, and have
`email_verified=false` (independent post-release AdminGetUser readback). They have
exactly `studio-research` / `studio-admin` membership respectively. No existing
user was reset or modified. Passwords are random and permanent for this expressly
approved administrator enrollment; no email or first-password setup required.

SSM SecureString paths (values never returned to model/chat):

- `/governed-agent-builder/qa/business/username`
- `/governed-agent-builder/qa/business/password`
- `/governed-agent-builder/qa/admin/username`
- `/governed-agent-builder/qa/admin/password`

Each QA enrollment expires 30 days after creation. See QA-ACCESS.md for the security
boundary and rotation/revocation procedure. Registry details remain in protected
release state, not this repository. Future standard releases must deliberately
carry forward the approved registry; omission fails QA access closed. Do not copy
an old enabled registry over a revoked enrollment.

## Code and package validation

- Auth/QA/email/serverless regression: **91 passed**.
- Tests repeated with the exact patched deployed Auth source: **71 passed**.
- Includes ordinary unverified denial; signed QA business/admin admission; forged
  signatures, cross-subject binding and role/workspace/group mismatch denial;
  cookie tampering; expiry; disabling/removing enrollment after session creation.
- These role-isolation tests use signed synthetic JWTs offline, not cloud QA login.
- Untracked model fault probes remained excluded from commits and release ZIPs.

| Existing function | Downloaded cloud CodeSha256 (base64) |
| --- | --- |
| Authorizer | `GFAGePCDy+WR9PqkCJDNvNFV+nP+8ZcAkZB57Ky113c=` |
| Business | `z7dleA3QXmv8vU9riHobj0bBglxYWghgMEtJFTVKXK8=` |
| Auth | `RPQjhtvNhwhzut7WkkVeEmA8Pu8wtCg5OeYBOjlJnSo=` |

Only hosted_auth.py plus qa_enrollment.py and protected qa_enrollments.json changed
inside the three packages. All other members matched original bytes; each function's
existing unrelated source differences were preserved. All Lambda environments,
physical resource IDs and checked Cognito policy fields are unchanged. No IAM,
CloudFormation, frontend, model/tool integration or fixture labels changed.

Fresh rollback ZIPs and original CodeSha256 values are in ignored
`artifacts/qa-access/`; the receipt records each completed update and independent
download verification. An initial ZIP metadata-copy bug was caught before any
Lambda write; corrected before all three successful updates.

## Cloud/API and browser scope

Post-release requests returned **401** for GET and POST `/api`, `/api/me`,
`/api/agents`, `/api/admin/catalog`, `/api/jobs/unknown`, plus GET
`/auth/verification/status`. Public config still reports CLOUD-HOSTED DEMO.

Actual browser inspection observed the existing Studio shell, Not signed in,
Sign in / Open Studio, followed by Cognito's email/password sign-in form. It did
not use a persona selector or bypass. **Authenticated business/admin browser and
cloud role-isolation acceptance remain unverified.** No secure host-owned
SSM-to-browser credential binding was available; no credentials were put into tool
arguments or retrieved into model context, and no session was injected. An
authorized protected UI-entry method (or human entry directly into Cognito) is
needed to finish this acceptance. This is not a model/tool integration acceptance.
