# Domain Harness source contract (compiler milestone)

This is **compiler-only acceptance**, not native execution, deployment readiness,
Gateway authentication proof, or a new business-specific agent loop. The prior
native feasibility audit (2026-09-11, `work/native-harness-feasibility.md` in the
parent workspace) is the design input; its integration gaps remain open below.
No cloud API, paid model, credential lookup, native invocation, or provisioning
is required by this module. Existing agent definitions, UI, and dedicated
`runtime/web_research` source remain unchanged. No fixture is used by live mode.

## Separation and trust

`backend/domain_harness_schema.py` defines immutable, extra-field-forbidden
Pydantic models. `docs/domain-harness.schema.json` publishes the business input
schema. `FoundationLibrary` is separate from Registry catalogs: it owns approved
source versions, supported generic capabilities, Gateway-only policy and caps.
`DomainHarnessDefinition` is a separate version model alongside, not a replacement
for, legacy `DefinitionInput`. Users select versioned catalog references and supply
their own system prompt, evaluation references and bounded limits. They cannot
supply roles, URLs, headers, credentials, native config, actor/session IDs or
allowlists. Prompts are content, never authority.

The trusted platform must resolve `FoundationLibrary`, `Catalogs` and
`AuthorizationContext` itself. These arguments are NOT authenticated merely
because they are typed. The offline CLI accepts local files for review only; it
is not an admission service and its output must never be accepted as a runtime
authorization token. There is deliberately no HTTP preview endpoint: production
catalog authority, version approval storage and fresh grant plumbing are not yet
wired, so adding one now would imply false production support.

Approval binds the complete domain digest. Each grant binds kind, exact resource
version, complete catalog-entry digest and current authorization epoch. Every
selection is checked for approval, workspace, version and grant. Owners cannot
cross workspaces; duplicate selections/catalog keys and ambiguous Gateway tool
names are rejected. Version changes require renewed domain approval; catalog
changes require renewed grants. Revocation is effective only when the future
trusted invoker re-resolves authority on **each** invocation/continuation. A saved
plan alone is never sufficient authority. No client-supplied role can elevate it.

## Frozen output

`compile_plan` is pure and deterministic. Its nested models/tuples are frozen:

- Foundation source digest/version and full approved contract;
- domain owner/workspace/version, user system prompt and explicit limits;
- exact target-qualified model route, protocol, Model Gateway reference and
  optional approved adapter/auth evidence;
- exact tool binding IDs/versions, Tool Gateway references and restrictive
  `@gateway_name/exact_operation` allowlist (empty means allow nothing);
- skill content digests/source references and explicit immutable-publication approval;
- independently versioned dataset/rubric content digests, source references and caps;
- authorization snapshot digest, sorted blockers and canonical plan/version digest.

Canonical hashes use UTF-8 JSON, sorted keys, compact separators, no NaN and no
clock/random data. Ordered selections are part of the approved version: changing
selection order intentionally changes its digest. Digests establish identity,
not provenance signatures. The trusted authority must verify actual artifact
bytes before approval. A digest-looking S3 prefix is not immutability proof.
Mutable Git branch/S3 locations are denied unless the authority explicitly approves
immutable publication. Runtime content verification and native skill loading
remain unimplemented. Shell/script skills are forbidden.

The preview is a whitelist of counts, capability names, protocol, digests, version
and blockers. It omits prompts, owner/workspace, source locations, route targets
and native config. No model is ever silently omitted to use direct Bedrock.

## Offline CLI and two compositions

The additive CLI leaves `python -m backend` and all existing agent endpoints alone:

```sh
.venv/bin/python -m backend.domain_harness_cli \
  --domain examples/domain_harness/domain-a.json \
  --foundations examples/domain_harness/foundations.json \
  --catalogs examples/domain_harness/catalogs.json \
  --authorization examples/domain_harness/authorization-a.json
```

Repeat with `domain-b.json` and `authorization-b.json`. Exit 0 means compilation
succeeded, NOT execution readiness. Exit 2 reports a redacted admission failure.
The examples are explicitly synthetic, compiler-only and have no real account
IDs, credentials, endpoints or executable content. Their source/content approvals
are illustrative local test inputs, not production approvals.

A selects Browser operation adapter binding + citation skill + webpage-analysis
prompt + Bedrock Claude messages route. B selects read-only MCP + policy grounding
skill + different prompt + Bedrock OpenAI chat-completions route. Both consume the
same Foundation source/version. B has neither a Browser selection nor a Browser
grant. No business code or Foundation rebuild occurs between compositions.
A's adapter reference is an interface contract, not an implemented adapter.

## Native wire boundary: schema validity is not policy readiness

`backend/native_harness_contract.py` only validates explicit specimens using
installed botocore `Loader`, `ServiceModel` and `ParamValidator`, without a
Session/client/credential lookup. It does not emit native config from plans.
The offline SDK at implementation is boto3/botocore 1.43.92.

- Create uses `harnessName`, `executionRoleArn`, explicit model and allowlist.
- Update uses `harnessId`; memory removal/disable uses `optionalValue` wrappers.
  Lists replace prior lists; omission must not accidentally retain prior authority.
- Invoke uses `harnessArn`, 33–100-character `runtimeSessionId`, messages and a
  named endpoint qualifier, not numeric version. No role, memory or environment.
- Tool allowlists cannot include shell, filesystem wildcards or
  `InvokeAgentRuntimeCommand`. Missing model/allowlist is rejected.

Tests deliberately include direct-Bedrock wire specimens to prove **wire validity
is not Gateway compliance**; all validator results have `execution_ready: false`.
Never submit these synthetic fixtures. SDK validation does not establish service
availability, ARN/resource existence, named endpoint immutability or authorization.
A future native emitter must bind create/update/invoke separately, pin resolved
native version evidence and enforce private networking and explicit memory intent.

## Remaining integration gates (no deployment claim)

1. **BLOCKED_MODEL_GATEWAY_AUTH** until an approved adapter route has native IAM
   or renewable OAuth evidence. The route approval/grant binds adapter, target,
   protocol and evidence digest together. A catalog claim alone is not runtime
   evidence. No static JWT-as-key, arbitrary headers, no-auth Gateway or direct
   Bedrock fallback. Peer work must verify streaming and tool-call round trips for
   both Bedrock Claude and Bedrock OpenAI through Model Gateway.
2. **BLOCKED_BROWSER_GATEWAY_ADAPTER** until each Browser operation goes through
   Tool Gateway and Policy to a verified native Browser adapter. No raw
   `agentcore_browser` attachment, ungoverned browsing sub-loop or fetch substitute.
   Prove session ownership, URL/redirect/private-address restrictions and egress.
3. **BLOCKED_TOOL_GATEWAY_POLICY** until exact tool allowlists and real Gateway
   Policy enforcement are verified; read-only MCP semantics need service evidence.
4. **BLOCKED_NATIVE_SKILL_LOADING** until restricted internal skill loading and
   exact content/version evidence are established without shell/file permissions.
   Native Git/S3 source fields do not themselves pin commits/object versions.
5. **BLOCKED_EVALUATION_GATEWAY_ROUTING**: dataset/rubric references are frozen,
   but no evaluator executes. Managed LLM judge Gateway routing remains unproven;
   deterministic/custom evaluator alternatives need separate acceptance. No PASS
   may be inferred from missing judge evidence.
6. **BLOCKED_NATIVE_INTEGRATION** remains unconditional in this source milestone:
   trusted authority persistence, fresh invocation admission, identity/session and
   memory isolation, explicit private network/role separation, endpoint/version
   resolution, budgets, streaming errors and actual runtime traces are unverified.
   Native config emission, production preview API and UI composition are deferred.

Even a test-supplied verified auth adapter clears only its individual auth gate;
`execution_ready` is a literal false in every output. No source fixture fallback
is connected to live execution. The parent coordinates native authentication
verification and fixed-SHA peer review separately.

## Verification and operational evidence

`tests/test_domain_harness.py` is independent of the dedicated research loop.
It checks deterministic repeated composition, actual compiler source hash, nested
immutability, redacted CLI output, schema parity, same-foundation A/B differences,
empty allowlists, denied overrides/cross-workspace/stale grants/unapproved routes,
missing Browser adapter, skill pin changes, bounds and distinct native SDK shapes.
Full-suite counts and exact reviewed commit are recorded in the source handoff;
real execution evidence is **NOT RUN**, not PASS. Frontend build is unnecessary
for this milestone because no frontend files changed.

Well-Architected: **Security** fails closed with native proof still blocked;
**Reliability** has deterministic offline contracts but no live continuation proof;
**Operational excellence** has pinned versions, redacted previews and testable gates;
**Performance efficiency** has no runtime measurements; **Cost optimization** has
bounded compiler limits but unverified native token-budget semantics;
**Sustainability** reuses a single generic compiler with no new always-on resources,
but no measured runtime improvement is claimed.

### Recorded source run

- Baseline: `0274e6123407f11a41781521e7484e4fbe03d086`, clean
  `feat/live-capabilities` checkout before changes.
- New independent tests: **48 passed**. Full suite: **404 passed**, 3 existing
  dependency/event-loop deprecation warnings, 16.24 seconds. Full suite ran with
  socket connections blocked and SDK config/credential files set to `/dev/null`.
- Shared Foundation source SHA-256:
  `58597dcfaf8c5e5ef943dbf1e0d343e6d6624db9c1fbb8160eb442e271cffbcf`.
- A plan digest:
  `fa810d80edc381bf7147fd5dd57e034457385a91252c191661c20984e4df0f19`.
- B plan digest:
  `8e3c741769de5462457b8760fa5cfc822de31c57b8b969a925c69b3d845751df`.
- Source hygiene scan: 13 added files; no literal account-number/common key
  signatures found. Publication guardrail: ALLOW; `git diff --check`: clean.
- Remote Git read confirmed the branch still at the baseline; default branch is
  `feat/local-first` (there is no `origin/main`). Anonymous Git read failed with
  authentication required while existing authenticated Git read succeeded:
  private-access check via Git transport, no repository metadata API used.
- Native execution, deployed Browser/MCP, skill fetching, judges and Gateway auth:
  **NOT RUN**. No frontend files changed; no frontend build claimed.

## Follow-up: mandatory Foundation identity and observability (contract v2)

The original milestone above did **not** include IdentityProfile or
ObservabilityProfile. The follow-up adds mandatory Foundation-owned profiles,
not domain options. Both synthetic domains now select Foundation version **2**
and domain version **2**; legacy source/plan digests in the earlier recorded run
are historical v1 evidence, not current v2 hashes. The plan's compiler contract
is `domain-harness-v2`. The Foundation JSON Schema is published separately in
`docs/foundation-definition.schema.json`. Old incomplete Foundations fail schema
validation rather than silently acquiring security defaults.

**Reusable contract is not reusable runtime implementation.** This source now
specifies the runtime foundation's identity, telemetry, memory, evaluation and
execution obligations. It still implements only admission and compilation; it
does not create a managed Harness, emit traces, authenticate a role, load memory,
or run an agent loop. Reuse AgentCore Identity and Observability adapters in the
future integration; no custom tracing stack or business-specific loop was added.

### Mandatory inherited profiles

| Foundation component | Enforced source contract | Runtime status |
|---|---|---|
| Identity | Verified server subject/workspace intent; owner/workspace/session binding; exact approved execution-role, Model/Tool Gateway and outbound-credential-policy refs; mandatory Gateway Policy; no client actor override or JWT-to-IAM conversion | NOT CONNECTED |
| Observability | AgentCore Observability; mandatory CloudWatch, OTEL and audit; fixed metadata redaction allowlist; audit separate from model reasoning; no raw credentials or full prompts by default; optional Splunk export requires an approved versioned ref plus approval digest | NOT CONNECTED |
| Memory | Explicit disabled default; namespace derived from owner/workspace/session; no cross-namespace access | NOT CONNECTED; native default must be explicitly disabled |
| Evaluation | Pinned Foundation baseline dataset/rubric and digests **plus** domain dataset/rubric; release evidence mandatory, never domain-only substitution | NOT CONNECTED; no evaluation result asserted |
| Execution | Foundation timeout/token/iteration/eval caps; bounded retries only for transient idempotent operations; never retry denials; total timeout includes retries | NOT CONNECTED; source bounds checked only |

`AuthorizationContext` now requires a trusted session record and exact role
binding. Admission checks that session owner/workspace match the domain and
trusted subject, and the selected role/model/tool gateways match Foundation
identity policy. This validates trusted references, **not** JWT signatures, actual
IAM role permission, credential renewal or session existence. The future adapter
must obtain this context from verified identity and server-owned session storage;
business request bodies must never provide it.

Observability's required metadata fields are trace ID, request ID, run ID,
Foundation/domain version, release binding digest, model/tool/Gateway/Browser
refs, event type, latency, token usage where available and error/denial categories.
Mandatory event categories are admission, model call, tool call, Browser
operation, evaluation, error, denial and completion. Telemetry must carry only
these structured fields, not arbitrary strings, prompt bodies, credential values
or model reasoning. No event-emitting or redacting runtime adapter exists yet:
these are adapter obligations, not a promise that arbitrary logs are safe.
Splunk configuration cannot add a URL, header or secret via this contract.

The release binding digest includes the complete Foundation, domain, baseline,
resolved dataset/rubric, model route, tool bindings and skills. Changing any of
those invalidates the binding; changing only session ID changes memory namespace
and admission/plan digest but not the release binding. A release binding is **not**
an evaluation result or a signed approval. Runtime evidence must cite this binding
before release can be accepted; no compiler path can claim release readiness.

New unconditional blockers: `BLOCKED_IDENTITY_RUNTIME_BINDING`,
`BLOCKED_OBSERVABILITY_PIPELINE`, `BLOCKED_MEMORY_ISOLATION`,
`BLOCKED_EVALUATION_RELEASE_EVIDENCE`, and `BLOCKED_EXECUTION_LIMIT_ENFORCEMENT`.
No config boolean can clear them. Existing Model Gateway auth, Browser, skill,
Policy and native integration gates remain in force.

Domains can only tighten existing limits; cannot modify retries, disable audit,
disable telemetry or replace identity/memory/evaluation profiles. Both compositions
inherit identical immutable profiles. Supported capabilities remain an explicit
known set (`browser`, `readonly_mcp`) for this slice, **not all possible agent
capabilities**. Extending that set needs a reviewed schema/catalog contract,
not another domain-specific Foundation loop.

### Recorded v2 source acceptance

- Compiler/profile test module: **87 tests** (39 added in this follow-up).
- Complete suite: **443 passed**, 3 existing deprecation warnings, 17.23 seconds,
  exit 0; socket connections blocked, SDK credential/config files `/dev/null`.
- Foundation version 2 source SHA-256:
  `c49be4250d762ced78f321662b6a1c8d3011b29af9cd142b7ea5518a2900bc6e`.
- A plan: `2b42455b1e8b1524dc094c3698fa675223479f87531452553f6a993b7680921b`.
- B plan: `c8c64481a2b58cfe230a8960571782aa89bc0ea15c353864613a3c1d80422f69`.
- Both inherit identical Foundation identity/observability profiles; both report
  `execution_ready: false`. Source scan: 10 changed/new files, zero bounded
  account/key-signature findings. No frontend changes or build; no cloud writes,
  runtime adapter connection, telemetry emission or production acceptance.
