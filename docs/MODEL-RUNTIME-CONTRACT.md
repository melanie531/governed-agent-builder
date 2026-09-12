# Model Runtime contract: source-only correction

Baseline inspected: `ceac041e3955c2057191816bcc4cb073b6b311c8`.
**That baseline is not approved for the Runtime-only requirement and must not be
promoted as compliant. No deployment or execution approval is produced here.**

## Policy provenance

The current peer review requires Runtime-only. Parent memory recall was reported
unavailable, and the earlier exact user “no Mantle” wording was not located in the
visible history. No earlier attribution/date is asserted. This scoped correction
intentionally rejects the old route pending owner policy review. It does not claim
AWS Mantle is unsupported globally; the general native ModelGateway provider is
unchanged. The new review proposal remains unapproved at source and exposure levels.

## Verified public contracts (read 2026-09-12)

- [GetFoundationModel](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_GetFoundationModel.html)
  returns native model identity, modalities, lifecycle, streaming and inference types.
  It does **not** return account entitlement, a Gateway route, or supported API names.
  The retained prior receipt has native ID `anthropic.claude-haiku-4-5-20251001-v1:0`
  and `INFERENCE_PROFILE`. Its timestamp/request ID are preserved, not freshly queried.
- [Haiku 4.5 card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-haiku-4-5.html)
  separately documents Runtime Messages, Converse and Invoke (not Responses or Chat
  Completions). In `us-west-2`, Geo/Global inference is supported, **not in-region**.
  Native foundation identity is not an approved inference-profile selection.
- [Runtime Converse](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html)
  and its [SDK schema](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-runtime/client/converse.html)
  use `POST /model/{modelId}/converse`, structured content blocks and `inferenceConfig`.
  [InvokeModel](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_InvokeModel.html)
  uses `/model/{modelId}/invoke` and a model-specific body. Neither is the Gateway
  `model`-in-body Messages routing contract. Path replacement does not translate them.
- [Runtime Messages](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-messages-api.html)
  **also documents a native `/anthropic/v1/messages` route**, with an Anthropic SDK
  example and API-key/bearer example. It describes Runtime IAM/SigV4 support generally;
  its concrete SigV4 example is InvokeModel. Thus Runtime is not inherently incompatible
  with Messages, but these examples alone do not verify this Gateway's IAM binding.
- [Gateway connectors](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-inference-connector.html)
  list `bedrock-mantle`, `openai`, `anthropic`, not a `bedrock-runtime` connector.
  [Provider contracts](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-inference-provider.html)
  support explicit HTTPS endpoints, `providerPath`, model matching and
  `GATEWAY_IAM_ROLE` SigV4; the Bedrock example uses Mantle. The
  [API shape](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_InferenceProviderTargetConfiguration.html)
  specifies endpoint/modelMapping/operations, not arbitrary body translation.

## Actual binding gap, not a placeholder success

Existing evidence is a **Mantle** target with a short Mantle ID. No reviewed actual
Gateway target binds the Runtime Messages endpoint, an exact allowed inference
profile, its region/residency, the required version headers, and the correct outbound
signing service/region and permissions. Generic provider schema acceptance and
model-card support do not establish that authenticated combination. A native Runtime
Messages provider is a research candidate, **not implemented or verified**. Converse
and Invoke require different request/response contracts; no guessed rewrite is added.
If the required binding needs an adapter, interceptor, new service or unsupported
body rewrite, that is out of scope. No direct Bedrock fallback is permitted here.

## Source enforcement

Schema v3 admits only owner-approved documentation with an exact native identity,
Runtime-only policy, supported APIs/region, prior GetFoundationModel projection hash,
card hash, expiry, and scope-bound approval digest. It has no mandatory Gateway or
Mantle fields, no endpoint/credential inputs, and no target-derived `model_id`.
Old schemas, mappings and route fields explicitly fail, even after rehashing. No old
configuration is auto-migrated or auto-approved. The proposal is a newly authored,
unapproved documentation candidate retaining only prior model evidence, not route
approval; hashes are integrity pins, not proof of authenticity or permission.

Every resulting row is documentation-only, unrequestable, execution/integration false,
with `NOT_CONFIGURED` and reason `gateway_runtime_binding_unverified`. Builder excludes
it from choices and rejects new selections even with exact Foundation pins. Historical
selections stay editable but remain non-deployable. The provider performs no SDK calls;
existing shared catalog STS binding logic is unchanged and was not invoked live here.

No cloud writes, metadata queries, paid calls, credential changes, grants, permission
changes, inference, deployment, PR or merge occurred. Offline test results and patch
scope are recorded in `PROVIDER-MODEL-METADATA-RESULT.md`.

Well-Architected: security fails closed without new authority; reliability rejects
stale/drifted evidence; operations expose the precise unresolved binding; performance
removes provider target reads; cost adds no paid calls; sustainability adds no compute.
Execution performance, reliability and permission readiness remain unverified.
