# Opus Messages codec subpatch — NOT an integrated executor

Parent wrote new foundation_harness/opus_messages.py only; existing config.py/model_client.py are reserved to the adapter worker, Studio/deployment driver to reviewer. No double-write.

build_request validates exact us.anthropic.claude-opus-5, integer1..256, text input/16KiB serialized byte bound, explicitly disabled thinking, no tools, no stream. read_response requires an explicit immutable tuple allowlist, exact provider identity, valid measured usage within cap, nonempty text-only completion; rejects thinking/tool blocks and does not infer IDs. No hardcoded real provider response ID. No AWS calls/credentials/transport in this module.

RED import failed before module existed; GREEN tests/test_opus_request_contract.py plus tests/test_foundation_executor.py:36 passed,2 existing warnings,0.16s. Synthetic response tests are not live inference. One-call/no-retry, manifest binding, authorization/reservation and telemetry remain caller responsibilities and are NOT implemented by this codec.

Integration still outstanding: config/model client must call this codec via approved manifest→authority/reservation→signed Gateway transport; actual runtime drivers and specified business/project permissions need end-to-end integration. Current old Model schema still rejects the passthrough binding and explicit request/response IDs; this new file alone does NOT fix it. IAM/Cedar/schema proposal from4b8128c remains unapplied, no deployed permission enlargement. No model invocation executed.
