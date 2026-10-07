# Scoped Global-Policy Release and Rollout Smoke

Reproducible procedure for the reviewed combined release (global-only model
policy + audited admin self-approval + MCP visibility) and its post-release
acceptance. Everything is parameterized: no account numbers, absolute paths,
pool ids or origins live in the scripts; credentials come from the operator's
AWS profile and chmod-600 files and are never printed or passed on argv.

## 1. Release (scripts/scoped_global_policy_release.py)

What it enforces, fail-closed:
- Proposed template may differ from the LIVE template by exactly one IAM
  statement: `bedrock:GetInferenceProfile` on
  `arn:aws:bedrock:*:<expected-account>:inference-profile/*` inside the
  BusinessRole `PlatformAdministration` policy. Any other delta aborts.
- The local `artifacts/serverless-release.zip` must hash to `--release-sha256`
  (the independently reviewed artifact).
- The evaluated change set may contain only: Modify (never replacement) of
  BusinessRole and the six functions, plus the benign reference cascade a
  Lambda code update causes (ApiGatewayV2 Integration/Authorizer entries whose
  every Detail is `ChangeSource=ResourceAttribute`, caused by one of the six
  functions, `RequiresRecreation=Never`; empty Details rejected).
- After execution: stack must reach UPDATE_COMPLETE, outputs must be unchanged
  (application identity), and all six deployed CodeSha256 values must equal
  the reviewed sha before the state file records the release.

Run (version-pinned checkout, operator-approved profile):

    python scripts/scoped_global_policy_release.py \
      --expected-account <12-digit account> --profile <aws profile> \
      --region <region> --state <fresh target-state.json> \
      --release-sha256 <sha256 of reviewed zip> --evidence <evidence dir>

Evidence written: template-before/proposed.json, change-set.json, receipt.json.
Guard regressions: `tests/test_scoped_global_policy_release.py`.

## 2. Rollout smoke (frontend/e2e-accept/rollout-smoke.spec.ts)

Covers, against the live deployment through governed endpoints only:
1. Discovery lists only `global.*` inference profiles; registering a regional
   or forged id fails 422 before any write.
2. A role-switcher files their own request business-active, is denied the
   decision endpoint while business-active (403), self-approves as active
   Platform Admin (200), and the audit row records
   `self_approved=true, actor_role=admin, actor==requester`. The synthetic
   grant is reverted via the governed revoke in the same test.
3. A plain business user gets 403 on the decision endpoint.

The self-approval test needs a temporary Cognito role-switcher (plain admins
cannot file capability requests). Create it, run the smoke, delete it:

    python scripts/e2e_switcher_identity.py --expected-account <account> \
      --profile <profile> --region <region> --user-pool-id <UserPoolId output> \
      --email <synthetic e-mail> --cred-out <chmod-600 file>

    (cd frontend && ACCEPT_ORIGIN=<ApplicationOrigin output> \
      E2E_CRED=<qa cred file> E2E_CRED_SMOKE=<switcher cred file> \
      npx playwright test --config playwright.rollout.config.ts)

    python scripts/e2e_switcher_identity.py ... --delete

Credential files are KEY=VALUE lines (ADMIN_USER/ADMIN_PW,
RESEARCH_USER/RESEARCH_PW; SMOKE_USER/SMOKE_PW for the switcher), chmod 600,
kept outside the repository. Evidence JSON/screenshots land in
`$SMOKE_EVIDENCE` (default `./accept-evidence`); keep evidence out of git.
