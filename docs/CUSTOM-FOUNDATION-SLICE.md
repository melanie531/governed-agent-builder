# Owned custom foundation: first executable slice

The new code executes a generic bounded Python model/tool loop. It is separate
from the historical domain compiler and web-research Runtime. The existing
Cloudscape application still runs its fixture driver. Production entry remains
fail closed; this is not a completed live user journey.

## Run the local proof

```bash
uv sync --locked --extra foundation-runtime
uv run --locked --extra foundation-runtime pytest -q
uv run --locked --extra foundation-runtime python -m scripts.foundation_probe \
  --output-dir artifacts/foundation-review
```

Use a fresh output directory. The driver saves a digest-addressed `harness.json`
configuration, packages source deterministically and runs the actual executor
with explicitly synthetic transport responses. It checks a forged caller before
dispatch. `offline-proof.json` records the source/manifest/package digests and
the observed local results. Fake usage is labelled OFFLINE; no local trace ID
is presented as a CloudWatch trace.

`harness.json` follows the configuration composition pattern illustrated in
[AWS Models and instructions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-models.html):
model, systemPrompt, tools, allowedTools, skills and limits. Its
`owned-foundation-v1` schema and digest/reference extensions are **owned
application fields**, not a claim of an AWS-accepted managed Harness request or
a verified public schema for that filename. Invocation overrides are deliberately
excluded. No managed Harness API, command API or executable skill is called.

`foundation_harness/config.py` validates references and data. Instruction skills
carry immutable version/digest bindings and have no code loader. Model/tool
Gateways are distinct, exact dedicated us-west-2 endpoints. The selected Claude
route is target-qualified. No direct provider fallback exists.

`admission.py` redeems once, verifies manifest/foundation bindings and rechecks
authority before every model/tool/discovery request and final publication.
`backend/foundation_authority.py` is an in-memory **controlled test driver**
authority, not Cognito or production authentication. No HTTP route, Runtime
payload or environment switch enables it. `runtime/custom_foundation/main.py`
offers an injected authenticated resolver seam; its default entry refuses to
run until the supported backend integration exists. An opaque run reference
alone confers no authority.

`engine.py` preserves tool-use IDs and results in subsequent turns. Limits cover
serial iterations, model/tool/Gateway calls, UTF-8 bytes, actual provider usage,
deadline and cooperative cancellation. In-flight HTTP has a total deadline;
cancellation stops subsequent dispatch and result publication. Request bytes plus
a conservative protocol allowance reserve input capacity; this is not an exact
provider tokenizer. No retry occurs after an uncertain inference outcome.
Production budget admission must retain the reservation when usage is unknown.

`tool_client.py` negotiates MCP 2025-03-26, permits one discovery page, intersects
the pinned selection and checks exact schema digests/arguments. External schema
references are prohibited. First-slice content is bounded text only. The synthetic
fixture has no network or AWS permissions and accepts only the `sample` key.
It proves neither Browser behavior nor egress enforcement.

## Save and deploy boundaries

`scripts/package_foundation.py` saves immutable configuration **before** any
Runtime exists. It exports only enumerated foundation/entry/lock files. Different
configurations share the same source digest; the source ZIP digest also binds the
configuration. This is a **source package**, not an already built Linux ARM64
artifact. Install its hash-locked dependencies in an approved Linux ARM64 build
environment before upload. No compatible build or image was claimed.

`backend/foundation_deployment.py` exposes Create-only new-agent submission through
the existing `RuntimeDeploymentAdapter`, retaining exact account, role, S3 version
and VPC guards. An immutable saved manifest and exact source/manifest package
bindings are required. Deployment key + config determines the idempotent Runtime
name/token; returned ARN/version are outputs. The existing compiler's
`execution_ready=False` and fixture worker are unchanged. Durable Studio DDB/SQS
orchestration, full artifact attestation and production workload redemption remain
unconnected; the adapter alone does not establish release readiness.

## Project provisioning and gates

```bash
# Offline default, creates nothing:
uv run --locked --extra foundation-runtime python -m scripts.foundation_cloud

# Each cloud action uses ONLY agentic-platform-prod / us-west-2.
# Existing stack is never silently updated or adopted:
uv run --locked --extra foundation-runtime python -m scripts.foundation_cloud prepare
uv run --locked --extra foundation-runtime python -m scripts.foundation_cloud inspect
uv run --locked --extra foundation-runtime python -m scripts.foundation_cloud execute
uv run --locked --extra foundation-runtime python -m scripts.foundation_cloud status

# Reads current target then exits 2 with explicit blockers; no inference:
uv run --locked --extra foundation-runtime python -m scripts.foundation_probe --live
```

Before each write, `foundation_target.py` compares STS internally with current
Studio stack ARNs, the exact distribution domain, CloudFront ARN and Cognito pool
ARN. It never logs account IDs or fetches users/secrets. The initial ChangeSet
must have the expected template digest and exactly its declared Add-only resources.
The explicit `prepare-repair` / `inspect-repair` / `execute-repair` actions only
accept this increment's exact failed initial template, the reviewed logical
resources and CloudFormation's unchanged tag/attribute dependency entries.
Current/ChangeSet tags must match; no direct property changes outside the reviewed
template correction are accepted. Only the two
resources verified as CREATE_FAILED (ModelTarget and ToolPolicy) can be replaced;
no healthy resource replacement or Remove action is permitted. There is no
general stack adoption or update mode.
Normal sandbox escalation may be required; denied actions must be recorded.

The separate stack contains TWO dedicated Gateways, Policy ENFORCE, a single
Claude target and one network-free synthetic Lambda target. The tool permit
names the exact Gateway/tool action and foundation workload. **Model Policy
coverage is unverified**: the available schema docs describe MCP tool actions.
The Model Gateway execution role explicitly denies Bedrock and Mantle
`CreateInference` while this gap is open. Metadata-only
`bedrock-mantle:ListModels` is scoped to the exact current account/region
`project/default`, because inference-target discovery needs it. This is distinct
from a grant to invoke models or modify that project. Its target being READY
cannot imply that inference is authorized.
No legacy Gateway was changed, invoked or reused.

The Runtime workload has exact Gateway invocation permissions, no administrative
grants, no direct Bedrock/Browser/command access, and only the documented
resource-unscoped `xray:PutTraceSegments` exception. Gateway roles are separate
from it and from each other. No arbitrary PassRole or shared identity changes
are included. No VPC/NAT/GPU/provisioned capacity is created.

## Cost and evidence

The first **live** proof is limited to one attempted inference, 2,000 input and
256 output tokens, with no retry after an uncertain outcome. The offline proof
uses two fake model responses to test the continuation loop and consumes none of
that live allowance. `reserve_once` writes a durable exclusive attempt latch
before dispatch; every nonzero dimension needs an applicable rate/source/date.
Unknown rates and billed usage are not zero. The US$5 estimate ceiling is not an
AWS billing hard cap. Production dispatch is blocked until all prerequisites,
including complete applicable rates, are established.

Manual OTel SDK spans carry only allowlisted metadata. Exceptions, raw prompts,
tool text and automatic SDK capture are excluded. The real exporter encodes
OTLP/protobuf, signs exact bytes for X-Ray us-west-2, bounds bytes/deadline, and
checks failures/partial rejection without retries. No global CloudWatch
transaction-search or identity settings are mutated. The exporter explicitly
targets the owned seven-day log group with `x-aws-log-group`/`x-aws-log-stream`
headers; it does not intentionally fall back to shared `aws/spans`. Its scoped
X-Ray delivery resource policy and account transaction-search state remain
unverified and are additional live-export gates. Export acceptance alone is
not readable-span evidence. Live export/readback remains NOT_RUN until measured.
Splunk delivery is also NOT_RUN.

Actual synthetic content, if a future admitted call occurs, belongs separately
in the owned private evidence bucket under `proof/`, with aggregate retained
content at most 1 MiB. No content was uploaded in this increment. S3 AES256,
bucket-owner enforcement, all BPA flags, and TLS-only policy are mandatory.
Log retention is seven days. S3 uses six-day current expiration plus one-day
noncurrent expiration; lifecycle runs asynchronously, so review/explicit owned
cleanup is required at seven days. Use unique content keys, no repeated versions.
No object version may be kept indefinitely by a delete marker.

Cleanup owner: project operator. Review/cleanup due **2026-09-19 Sydney**.
Use the stack's exact owned resources from its resource list. After review,
delete this owned stack through an inspected project change/cleanup procedure;
empty/delete only its retained Evidence bucket (including object versions) and
Operations log group if no longer needed. They have Retain policies to avoid
silent evidence loss. Remove only the corresponding local ignored artifacts.
No shared resources or old demo Gateways are cleanup targets. Cleanup was NOT_RUN.

## Evidence and official contracts

Actual deployment state, exact sanitized errors, test counts and remaining work
are recorded in [NIGHTLY-STATUS.md](NIGHTLY-STATUS.md).
Sources retrieved 2026-09-12 Sydney:

- [Inference provider routing and SigV4](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-inference-provider.html)
- [Haiku 4.5 endpoint/model IDs](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-haiku-4-5.html)
- [Policy schema constraints](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-schema-constraints.html)
- [Policy execution and management IAM](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-permissions.html)
- [Mantle IAM actions and project/model scope](https://docs.aws.amazon.com/service-authorization/latest/reference/list_bedrock-mantle.html)
- [OTLP endpoint and signing](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-OTLPEndpoint.html)
- [AgentCore prices](https://aws.amazon.com/bedrock/agentcore/pricing/)
- [Bedrock](https://aws.amazon.com/bedrock/pricing/), [S3](https://aws.amazon.com/s3/pricing/),
  [Lambda](https://aws.amazon.com/lambda/pricing/) and [CloudWatch](https://aws.amazon.com/cloudwatch/pricing/)
  pricing pages were retrieved; exact applicable rates for all proof dimensions
  were not established. No guessed rate admits inference.

Next milestones: actual Browser adapter with all-path egress/read-only proof;
same foundation image with two immutable configurations; native evaluator Lambda
and Gateway judge with authenticated evidence; live UI catalogs, per-agent
DDB/SQS lifecycle, permission requests/approvals, costs, observability and versions.
