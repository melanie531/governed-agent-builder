# Opus passthrough transport dispatch subpatch — review branch

Scope: foundation_harness/config.py + tests/test_opus_transport_dispatch.py only, on top of the existing reviewed subpatch chain (cbd0bab: Model schema `messages-passthrough` + ModelClient codec wiring). Studio/backend registration, execution driver, IAM, Cedar and deployment remain reviewer-owned. No cloud writes, no model invocation, no Ready flag changes.

## Gap this closes

`IAMTransport.post` routes every non-MCP URL through the shared `config.endpoint` guard pinned to the legacy `/inference/v1/messages` path. The reviewed passthrough binding (`/bedrockrt/v1/messages`, exact request model `us.anthropic.claude-opus-5`, explicit `responseModelAllowlist`) validated at the manifest layer but every actual dispatch would raise `DEDICATED_GATEWAY_ENDPOINT_REQUIRED` before signing. RED probe `tests/test_opus_transport_dispatch.py::test_transport_dispatch_admits_reviewed_passthrough_messages_path` failed at cbd0bab (log: red-transport-dispatch.log — 1 failed, 8 passed).

## Change

- `exact_endpoint(value, *paths)`: fullmatch of the dedicated `gab-foundation-*.gateway.bedrock-agentcore.us-west-2.amazonaws.com` host against exact reviewed paths.
- `endpoint(value, path)` (transport dispatch guard, signature unchanged): the legacy Messages dispatch literal now admits both reviewed Messages target paths via `MESSAGES_DISPATCH_PATHS = ('/inference/v1/messages', '/bedrockrt/v1/messages')`; every other path stays exact. `/bedrockrt/v1/messages` is the inbound path of the existing READY HTTP passthrough target `bedrockrt` from real read-only GetGatewayTarget evidence (endpoint `https://bedrock-runtime.us-west-2.amazonaws.com/anthropic`, `/v1/messages` schema, GATEWAY_IAM_ROLE service=bedrock), not a guessed recipe.
- `Model.check` switches both protocol branches to `exact_endpoint`, so widening the dispatch guard does NOT let a legacy `messages` manifest carry the passthrough path (or vice versa). Manifest digest semantics unchanged; legacy manifests serialize identically.

No response-identity inference anywhere: legacy prefix-strip comparison remains confined to the legacy branch (untouched here; retirement is a separate reviewed step), passthrough responses go only through the explicit allowlist in `opus_messages.read_response`. Test allowlists are clearly synthetic (`synthetic-opus-response`); the real Opus response model ID is still uncaptured, so no production allowlist value is shipped.

## Tests (all local synthetic; no live evidence claimed)

- RED at cbd0bab: `red-transport-dispatch.log` (1 failed, 8 passed). Also re-verified the parent commit's own RED: reverting config.py/model_client.py to a9159da fails 3 opus modelclient tests (`red-base-a9159da.log`).
- GREEN: `tests/test_opus_transport_dispatch.py tests/test_opus_modelclient.py tests/test_opus_request_contract.py tests/test_foundation_executor.py tests/test_foundation_approval.py` → 70 passed (`green-targeted.log`).
- Regression across all consumers of the shared endpoint guard (wiring, delivery, producer, admission, telemetry flush, evidence exporters, initialization, evaluators, live preflight): 285 passed (`green-regression-endpoint-consumers.log`).
- Negative coverage: direct bedrock-runtime host, non-Gateway host, `/anthropic/v1/messages` on the Gateway host, path suffixes, `/mcp` cross-path, legacy manifest + passthrough endpoint, passthrough manifest + legacy endpoint — all rejected; credential-less transport still fails closed with `WORKLOAD_CREDENTIALS_REQUIRED` after the URL gate.

Logs copied under docs/model-invocation/opus-dispatch-logs/.

## Not done / next (owner: reviewer/brother)

- Real Opus response model ID capture from an authorized live call → production `responseModelAllowlist` value (prod stays OFF until then).
- IAM/Cedar activation per docs/model-invocation/OPUS-FIRST-REVIEW.md (candidate only, NOT applied).
- Studio registration/driver wiring of a passthrough manifest; live acceptance. This branch is review-only; red/green facts above are local synthetic runs, never deployment or readiness evidence.
