# Model Catalog metadata contract blocker (2026-09-12)

## Result

The requested real ModelGateway → Catalog → Builder function is **not delivered**.
The exact approved Gateway metadata GET fails before model enumeration. No model
rows, Foundation bindings, grants, Business IAM or backend/frontend changes were
published. This is a live service-contract blocker, not an unwired adapter claim.

Baseline verified: `feat/catalog-readable-columns`,
`67e64fbad04ffd810482a13cfd728e19ea22b790`. Work is published on
`feat/studio-model-metadata`; no PR, merge, force push, Runtime or inference calls.
Unrelated untracked model-gate fault files were preserved.

## Actual controlled evidence

- Explicit `agentic-platform-prod`, `us-west-2` STS privately matched the existing
  Studio CloudFormation, CloudFront and Cognito identities before writes.
- Actual GetGatewayTarget: project-owned target `claude`, status READY, provider
  `https://bedrock-mantle.us-west-2.api.aws`, configured operation `/v1/messages`
  → `/anthropic/v1/messages`, exact configured model `anthropic.claude-haiku-4-5`.
  This is **target configuration only**, not successful model enumeration or execution.
- Official documentation specifies GET `/inference/v1/models`. First actual
  operator SigV4 GET returned HTTP 403 `MODEL_GATE_DENIED`.
- A tested exception for **exact GET `/inference/v1/models`, empty body only**
  was deployed through a non-replacing changeset. Real GET still returned 403.
- A second narrow code-only release added finite structural diagnostics (never
  headers, credentials, raw paths or body contents). The actual signed GET was
  delivered to the interceptor as **POST `/v1/models` with a null body**.
  Correlated Gateway request ID: `54b714f2-8144-4b31-b427-9d02d875ed01`.
- This contradicts the assumption that the interceptor's immutable httpMethod
  represents the incoming GET. Allowing this POST-shaped event would not be the
  authorized exact-GET exception. No such exception was added.
- Original interceptor was restored and the restore completed successfully.
  Exact live index.py SHA-256:
  `84718a6d19b39659a420db0811990a9d0f2ce0ea6bf79ed0ccec00a091632669`.
  This equals the original deployed/source gate. Restored ZIP SHA-256:
  `7ccdaa5f0fb44ad7d21eeae5c4a380cbc19681ebee7d6dbf2585f155e0b997c3`.
  Restore readback GET still returns HTTP 403 `MODEL_GATE_DENIED`, as expected.

All three executed updates changed only ModelGate code. CloudFormation also
reported dynamic dependencies; exact byte-identical templates and full resolved
IAM policies/trust, Gateway ENFORCE/interceptor bindings, and ToolPolicy Cedar
were checked before/after. All physical IDs and outputs stayed unchanged.
CreateInference Deny and every POST inference validation remain original.
An initial overly narrow changeset guard rejected dynamic dependencies before
execution; only explicitly enumerated identical dependencies were then accepted.

72 focused tests passed after restoration, including rejection of the actual
POST-shaped metadata event. No positive inference or provider calls, new grants,
user/credential/SSM mutations, tool calls or Runtime creation were performed.
Only the existing owned foundation stack was updated. Studio Business, Registry,
QA enrollment, telemetry and persisted user/draft state were not mutated.

## Existing frontend preservation, not new UI acceptance

Actual CloudFront index and both referenced assets match existing local dist:

- index SHA-256 `9e1f8677c78179ddc103e2ebe9498f3bd3397242a0d39b53e03f6066bb7bb168`
- JS SHA-256 `dcf2d0139c013383c21b627dcf2821479fa646c50ae018c2cf327a5be538e672`
- CSS SHA-256 `5c84df3bbbcbe2732de233ecd74b29de31e62a21acc036ed9a4f28138d42c956`

No new frontend assets were uploaded. No hosted QA login or new Builder dropdown
acceptance was performed in this slice, because there is no enumerated model to
expose or select. Prior QA evidence is not relabelled as current acceptance.

## Exact next gate

Resolve why Gateway's documented model-list GET reaches HTTP interception as
POST `/v1/models`, null body, or establish a separately reviewed dedicated
metadata provider that can enumerate actual models without that ambiguous
intercepted invocation. Do not pass arbitrary GET/POST, infer model inventory
from target mappings/globs, or remove inference Deny/ENFORCE. Business
InvokeGateway permission is not GET-only and was deliberately not granted.

Once enumeration is proven: approve full-v2 model hashes and workspace exposure,
bind stable real IDs through reviewed Foundation native_bindings, preserve
requestable-versus-granted access, deploy Business-only code/config, and run the
existing secure host QA flow for native Catalog/Builder unsaved draft selection.
Execution must remain NotReady. Actual Runtime binding and positive execution
review are subsequent gates, not achieved here.

Private receipts (ignored, do not commit): `artifacts/model-catalog/`,
`artifacts/model-catalog-structure/`, `artifacts/model-catalog-restore/`.
The restoration process `gentle-cove` completed with exit 0; no worker remains
running. Do not rerun the one-shot release script over its existing receipts.

References:
- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-inference-connector.html
- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-interceptors-types.html

Well-Architected: security preserved and restored; reliability fails closed on
unproven transport; operations have source/ZIP/template readback receipts;
performance remains unmeasured; cost is metadata/deployment only with no inference;
sustainability uses no new persistent compute. No whole-platform/MVP success claim.
