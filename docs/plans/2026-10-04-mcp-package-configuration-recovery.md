# MCP package configuration recovery

## Observed failure

The user's `sf_mcp_demo` upload `7ca85d9803934c42894b1917108517fa`
contains the verified complete Snowflake ZIP
(`fedb7b4ee17a6bc3d9d5b1b7f123e386cefe1354b81c6ca8e883a82f0f39bd0d`).
Its Runtime was created with the uploader's default Generic/IAM configuration,
without Snowflake environment settings. Tool discovery fails during MCP
initialization. Running the same server startup without these settings raises
`KeyError: 'SNOWFLAKE_ACCOUNT'`; supplying the account, reader role, warehouse
and Gateway token source makes startup succeed.

The successful earlier hosted package test explicitly selected the Snowflake
configuration. It did not cover accepting the uploader's silent Generic default.

## Scope and sequence

1. Preserve the failed upload and its immutable archive. Record the native
   configuration, checksum comparison and minimal startup reproduction.
2. Add failing browser tests for an explicit configuration choice, the complete
   Snowflake upload request, retained configuration visibility and actionable
   tool-discovery failure. Add backend coverage for bounded discovery failure
   codes and public stage metadata.
3. Remove the form's default configuration. Explain which configuration the
   supplied Snowflake ZIP needs, show the selected configuration while work is
   retained, and distinguish server initialization/discovery failures from ZIP
   rejection. Keep OAuth 3LO and 2LO in their separate later onboarding paths.
4. Keep security validation applicable to rejected uploads and partially
   completed deployments: verify resources that exist and absence where no
   deployment was attempted. Preserve application failures explicitly in the
   audit instead of requiring every historical attempt to be READY.
5. Run the focused regression tests, broader affected suites, frontend build,
   compilation and diff checks. Build the Lambda artifact from the current
   immutable release, replacing only changed owned members.
6. Run the AWS security/tag audit, release the scoped change to the existing
   authorized Studio deployment, verify terminal release status, and repeat
   the security/private-access/tag checks.
7. Use the existing authenticated Chrome session for a fresh complete Snowflake
   ZIP upload with its explicit configuration. Verify READY, exact uploaded
   bytes, seven tools and the next authentication screen. Preserve the old
   attempt; do not rewrite immutable deployment settings or delete resources.

## Acceptance boundaries

The immediate acceptance target is the user's package deployment and entry into
the correct authentication step. Tool discovery does not prove Snowflake data
access or a new agent invocation. Catalog publication requires available quota;
no existing registrations will be removed and no quota will be raised under
this repair. A new query must be a fresh user-owned action, not a replay.

Browser plugin is not available. Use the existing repository Playwright suite
for rendered regression coverage and the existing authenticated Chrome session
for hosted acceptance. Store sanitized evidence under
`artifacts/studio-mcp-package-configuration-20261004/`.

## Progress

- [x] Exact failed stage, archive identity and startup configuration reproduced.
- [x] Regression tests demonstrate the UI and error-reporting gaps.
- [x] Scoped implementation and affected checks pass.
- [x] Scoped release, security audit and hosted acceptance pass.

## Verified result

The existing Studio now serves `assets/index-DDt0IbXT.js`; its six existing
Lambda functions use release
`ed8385587e6824cc04ebea0bccf5c8e8a63abc4983eec27c1048bf9db503c85e`.
Only the two Python deployment modules changed in the Lambda ZIP; 4,072 other
members match the previous release.

The fresh authenticated upload `12355187be1d412e9a1f9ca0417ac73e`, named
`sf_mcp_demo`, reached READY with all seven Snowflake tools. All 15 upload parts
and final deployment submission succeeded through the rendered form. Native
Runtime version 1 and its S3 checksum match the submitted ZIP. Studio advanced
to the separate OAuth 3LO step.

Validation: 2,143 backend tests passed, four skipped; 48 browser scenarios
passed; six final package/visual checks passed. The pre-release security audit
checked 77 resources and the post-release audit checked 82, both passing.
Historical failed attempts retain their actual statuses. The new connection's
OAuth consent, catalog publication and agent query were not performed.

See `artifacts/studio-mcp-package-configuration-20261004/VALIDATION.md`.
