# Opus 5 foundation package — ARM64 execution record

## ⚠️ EMULATED Linux ARM64 (binfmt/qemu) — NOT equivalent to AgentCore on-host acceptance
This run is qemu-aarch64 emulation via Docker binfmt on an x86_64 host. It proves
the packaged native aarch64 wheels load and the codec runs on aarch64 instruction
semantics. It is NOT native ARM hardware and NOT AgentCore Runtime on-host
acceptance. Do not read emulated success as native/live readiness.

## Record (all required fields)
- Foundation package sha256: `a9cc498651656cc954b18b3b1c8b23451f57c22d0467be1ff10ae0fd408e8c9b`
- Foundation package bytes: 27,513,146 (mode=base, artifact_kind=linux-arm64-python3.13-locked, deploy_ready=false)
- Docker image: `arm64v8/python:3.13-slim`
- Docker image digest: `arm64v8/python@sha256:c33ba31a4f8fc187a1ec9f33ef63945c29e9eaa74c47542894f26db67a4156d6`
- Run isolation: `docker run --rm --network none --platform linux/arm64` (offline; no network, no cloud)
- In-container arch (`platform.machine()`): `aarch64`
- In-container Python: `3.13.15`
- In-container package sha256 (re-hashed inside container): `a9cc498651656cc954b18b3b1c8b23451f57c22d0467be1ff10ae0fd408e8c9b` (matches host)
- Container exit code: `0`

## What executed inside the container
- Native aarch64 wheels imported: pydantic_core, pydantic, jsonschema, boto3 → OK
- foundation_harness.opus_messages.build_request → stream=false, thinking disabled, no tools → OK
- Codec fail-closed proven on ARM64:
  - empty allowlist → EXPLICIT_RESPONSE_ID_ALLOWLIST_REQUIRED (reject)
  - unknown response id → OPUS_RESPONSE_IDENTITY_MISMATCH (reject)
  - pinned/known id → accepted
- Final: `ARM64_EXECUTION_PROOF=ALL_PASSED`

## No billable cloud resource created
Only local Docker (existing daemon) + qemu binfmt were used. AWS calls limited to
reads: `sts get-caller-identity`, `ssm get-parameter`. No EC2/CodeBuild/Runtime/etc
was created. Native (non-emulated) Graviton acceptance still requires an
already-provisioned ARM64 host/container platform — not self-provisioned here.
