# Provider model metadata: source-only result (2026-09-12)

Baseline: `4909b4dedd94ef192b85dc9aa20d606fed0b53cd`, branch `feat/provider-model-metadata`.
No website deployment or activation. No driver, FoundationJobs, IAM, interceptor,
ENFORCE, CreateInference Deny, grants, auth, or Deploy enabled-state changes.

## Real evidence

STS account matched the existing Studio app CloudFormation StackId and recorded
project Gateway ARN; all regions matched `us-west-2`, project tag matched.
Only profile `agentic-platform-prod` was used. No account switch or secret reads.

- `bedrock:GetFoundationModel` succeeded, request ID `8cc1c717-6293-4684-aa9f-91c2b34f5db3`.
  Exact ID `anthropic.claude-haiku-4-5-20251001-v1:0`, provider Anthropic,
  lifecycle ACTIVE, TEXT/IMAGE input, TEXT output, INFERENCE_PROFILE metadata.
  This is not proof of account entitlement or Mantle execution.
- `GetGatewayTarget` succeeded, request ID `ae63bc52-14df-4709-a23a-37a03c97b559`.
  Existing target `claude`, status READY, Mantle endpoint in us-west-2,
  exact `/v1/messages` → `/anthropic/v1/messages`, configured model
  `anthropic.claude-haiku-4-5`. READY describes configuration, not execution.
- Official card: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-haiku-4-5.html
  Explicitly corroborates both distinct IDs, Mantle Messages (not Responses),
  and us-west-2 availability. No model-ID conversion heuristic.
- Captured `2026-09-12T08:46:28.563652+00:00`; expires
  `2026-09-13T08:46:28.563652+00:00` (maximum 24h).
  Card normalized markdown SHA-256: `6f5fdc77a67dc7ec18bd974a28b79bae791e873be7848856334b5422e4267831`.
  Target identity/config SHA-256: `fc8d1d433f46308d999f08d202a4ec9599d23936934a48583e21a8c063e4528d`.
  Approved-source candidate SHA-256: `f9fa359c20f7f6dc0728ed57c192e12df1ddbd4813bb77b2d9da1b8806ad0779`.

## Implementation and pending activation delta

`backend/provider_model_metadata.py` is a distinct, single-model provider, wired
through optional server-owned `NATIVE_CATALOG_CONFIG.provider_metadata`.
Its source digest includes full private Gateway identity, target configuration
revision, provider/control-plane evidence, official mapping, retrieval/expiry,
and exact workspace/requestability/owner/data-handling scope. No browser input
can supply this configuration. Arbitrary IDs, different families/APIs/regions,
expired snapshots, changed scopes and target drift fail closed. Each read checks
the exact current target; provider snapshots deliberately bypass catalog cache.
No automatic provider-list-to-execution or fixture fallback exists.

Public rows carry exactly:
`AWS provider metadata + configured Gateway route; execution unverified`.
Separate `ModelGateway` enumeration remains `NotConnected` with empty
`model_gateways`; `ProviderMetadata` is separate. UI displays provenance/expiry,
retains Add to draft (not deployment), and Builder options inherit provenance
in their description. integration_ready/execution_ready remain false; entitlement
is unverified. Existing explicit Foundation native_bindings is unchanged.

Review proposal: `docs/PROVIDER-MODEL-METADATA-PROPOSAL.json`. Both source and
exposure approvals are **false**. Committed ARN account is a placeholder, so this
review copy cannot activate. Exact private proposal and receipts live in ignored
`artifacts/provider-model-metadata/`; no full account IDs/credentials committed.

Owner must separately:
1. Review the exact private snapshot/card/target digest and source semantics.
   If expired, authorize a same-account/region refresh and recompute approvals.
2. Merge this source into existing v2 server-owned config (preserve Registry and
   existing binding; keep `model_gateways: []`), resolve the existing private
   Studio account ARN, explicitly approve source and exact exposure. Scope is
   **research only**, `requestable: false`; no grants are created.
3. Approve the chosen Foundation manifest's existing `native_bindings` contract
   with exact component below, preserving its other entries. No alias conversion:
   - id: `provider-model:gab-foundation-model-m0-gm7vvru7gn:5ADTYKAJSO:anthropic.claude-haiku-4-5`
   - version and source_revision: `f9fa359c20f7f6dc0728ed57c192e12df1ddbd4813bb77b2d9da1b8806ad0779`
4. Independently verify existing application read permission for exact
   GetGatewayTarget. Operator read success is NOT proof the Business role can read.
   If denied, retain NotConnected; no permission expansion is authorized here.
5. Separately release source/config/UI only after review and perform hosted QA.
   Website remains untouched in this task. Full real Model Gateway inference is
   the next execution gate, not achieved by this metadata implementation.

## Verification and exact errors

- 120 focused catalog/provider/Builder tests PASS, including HTTP Catalog → exact
  Foundation binding → saved unready draft with no jobs/grants; workspace hiding,
  unsupported operation/model/region, snapshot expiry/digest drift, target drift,
  STS mismatch, no SDK calls before config validation, no fixture fallback.
- Sequential `npm run build` PASS (TypeScript + Vite). Build warns chunks exceed
  500 kB. Two pre-existing Starlette/httpx/anyio deprecation warnings in tests.
- Actual saved receipts normalized successfully through the adapter locally;
  synthetic tests are not represented as live execution or hosted UI acceptance.
- No AWS metadata API denial. A diagnostic print AFTER both successful readbacks
  raised `TypeError: Object of type datetime is not JSON serializable`.
  Raw successful responses had already been saved; stable nonsecret normalization
  excludes datetime fields. Exact diagnostic retained privately in `error.json`.
- `git log origin/main..HEAD --oneline` reported unknown revision because this repo
  has no origin/main. Remote default is `feat/local-first`; intended authorized
  baseline is `origin/feat/builder-explicit-deploy` at the supplied SHA. Review
  outgoing commits against that baseline instead. No reset/rebase/force/PR/merge.
- Unrelated four untracked fault/release files preserved, not staged.

Well-Architected: security fail-closed and no new authority; reliability uses
expiry/drift rejection; operations retain request IDs and digests; performance
adds one bounded target read per catalog refresh (not benchmarked); cost only
metadata reads/build; sustainability no new persistent compute. Discovery is not
execution. Driver investigation remains entirely with the peer.
