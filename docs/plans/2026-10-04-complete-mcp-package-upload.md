# Complete MCP package upload

The current `.py` upload overlays an installed Snowflake bundle. Add a separate,
visible ZIP upload that deploys exactly the package supplied by the administrator.
Keep the existing endpoint and single-file paths compatible.

## Contract

- A deployable ZIP contains `main.py` at its root, supporting modules, and
  dependencies for Python 3.13 on Linux ARM64. It serves MCP Streamable HTTP on
  `0.0.0.0:8000/mcp`. Studio does not install requirements or inject source.
- Maximum archive size is 64 MiB; expanded size is at most 256 MiB.
- Authenticated, CSRF-protected chunk requests transfer the archive into private,
  encrypted, versioned S3 storage. No archive bytes enter DynamoDB, audit output,
  or browser storage.
- Reserve an immutable request before uploading. Bind every chunk to its digest,
  length, key and S3 version. Reconcile uncertain writes before an explicit retry.
  Finalization creates one existing durable Python deployment job.
- Validate paths, duplicate entries, entry types, compression, CRCs, expansion,
  native-library architecture, and root Python syntax before creating a Runtime.
  The final Runtime ZIP must have the uploaded SHA-256, without rewriting it.
- Generic packages use IAM inbound authentication and a scoped Gateway invocation
  grant. Snowflake-compatible packages can separately select the existing OAuth
  bridge and supply its non-secret account, reader-role and warehouse settings.
  User OAuth 3LO and service OAuth 2LO remain distinct connection steps.
- Extend the existing CDK-generated application with bounded upload, read and
  Gateway policies. Preserve the current app identity and installed bundle.

## Execution and verification

1. Add failing archive, API, native SDK and browser contract tests.
2. Implement bounded upload and validation, connect it to the existing job
   lifecycle, then add the ZIP form and recovery controls.
3. Add infrastructure/release guardrail tests and scoped policies. Run focused
   tests, broader affected backend/browser suites, compilation, frontend build,
   synthesis and `git diff --check`.
4. Build an independent small MCP package and the complete Snowflake package.
   Verify archive contents and digests.
5. Audit the current deployment, journal a scoped release based on the verified
   current Lambda archive, deploy the existing isolated Studio, and verify native
   terminal states and resource tags.
6. Test an actual complete package through the authenticated application; verify
   discovered tools, byte-identical deployment and a harmless standalone tool
   invocation. Capture sanitized requests/errors and desktop/mobile screenshots.
7. Provide the uploadable Snowflake ZIP, its source/build instructions and a
   numbered agent test journey. Report the existing catalog capacity separately;
   do not delete registrations or claim a fresh publication without evidence.

## Delivery evidence

Implemented and deployed to the existing Studio on 2026-10-04.
Evidence and uploadable ZIPs are in
`artifacts/studio-complete-mcp-package-20261004/`.

- Authenticated UI uploads deployed the independent generic package and the
  complete Snowflake package. Both Runtime ZIP hashes equal their uploaded
  archive hashes.
- The generic package contains no `snowflake_mcp/` code. Its supporting Python
  module and text resource returned the expected result through both native
  Runtime invocation and AgentCore Gateway.
- The full Snowflake ZIP reached READY and discovered all seven tools.
- Rendered desktop/mobile checks and reload recovery passed. The hosted
  observer recorded 95 successful requests with zero application errors.
- Post-deployment audit: 79 resource checks passed. Anonymous requests to the
  application API, package API, generic Runtime, Snowflake bridge and package
  storage were denied.
- The generic registration remains at REVIEW. Research has 35/35 catalog
  entries. New Snowflake publication needs eight available entries. No fresh
  agent publication or Snowflake data query is claimed for these uploads.

The user journey, build commands and agent prompts are in
`artifacts/studio-complete-mcp-package-20261004/UPLOAD-AND-TEST.md`.
