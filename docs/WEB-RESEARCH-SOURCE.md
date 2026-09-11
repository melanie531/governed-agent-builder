# Web research foundation v1.0.0: source-only, NOT_CONFIGURED

This slice implements executable HTTPS fetching, Tool Gateway MCP calls, Model
Gateway inference calls, immutable definition validation and report generation.
It does **not** deploy or attest a live service. The deployed website, Cognito,
data, prior versions and fixture executor are unchanged. No AWS read/write or
paid model call was performed to implement this slice.

## Artifacts and execution

- `foundations/web_research.py`: platform foundation and two packaged, versioned
  report instruction artifacts (cited brief/comparison), separate from Registry.
- `tools/web_fetch/handler.py`: Lambda target handler. `policy.json` defaults to
  deny all. `tool-schema.json` exposes only `url`, not policy or headers.
- `runtime/web_research/app.py`: actual AgentCore SDK application and entrypoint;
  imports and offline startup do not discover credentials or call AWS. Packaged
  manifest and execution config only; invocation cannot override inputs.
- `runtime/web_research/gateways.py`: IAM SigV4 HTTP transport, target-qualified
  MCP `tools/call` and Claude Messages inference through separate Gateways.
- `runtime/web_research/harness.py`: fetch each pinned URL first; make exactly one
  real model-adapter report call second. No pretend multi-agent loop and no
  fixture fallback. Model tool calls are refused, not executed.
- `scripts/web_research_smoke.py`: offline stub Gateway integration by default;
  captures the complete actual outgoing model prompt and MCP request. Stub
  report is supplied by the test, not computed from expected evaluation answers.
- `scripts/package_web_research.py`: executable source ZIP with `main.py`,
  definition, manifest, code and dependency lock. It is **not** a deploy-ready
  binary ZIP; dependencies must be installed for Linux ARM64 before upload.
- `runtime/web_research/Dockerfile`: container packaging recipe, not built or
  pushed in this slice. Uses the hash-locked runtime dependencies from `uv.lock`.

Offline commands (no cloud access):

```sh
cd frontend && npm run build && cd ..
UV_OFFLINE=1 uv run --locked --offline python -m pytest -q
UV_OFFLINE=1 uv run --locked --offline python scripts/web_research_smoke.py
cd frontend && UV_OFFLINE=1 npm run test:e2e
```

Use `--source-text` and `--model-response` files to exercise arbitrary provided
source/model outputs offline. Do not put real private data in committed test
artifacts. To generate a source ZIP, provide an approved pinned manifest to
`package_web_research.py --manifest ... --output ...`; existing output files
are never overwritten. No credentials are included in manifest/config/schema.

## Immutable bindings and UI semantics

The UI offers web research question, URLs, format, prompt, report skill, dataset
and rubric. Save produces an immutable **NOT_CONFIGURED** draft with exact input,
prompt/dataset/rubric hashes and packaged skill artifact digest. It allows no
fabricated model or tool binding. Existing optimistic version checks apply.
Execution, fixture export and fixture deploy/test reject research definitions.
No SQS job is enqueued for a source draft. Existing fixture flows remain labeled.

Before Runtime packaging, an operator must approve exact model/tool versions,
endpoints, model route, allowed domains and immutable Gateway target snapshots.
The execution manifest requires exact selected component bindings and definition
hash, foundation artifact hash, packaged skill hash and eval refs. Any stale or
unapproved version fails before tool/model use. The Runtime environment's
DEFINITION_DIGEST and MANIFEST_DIGEST must match the packaged artifact.

## Security policy and limits

- HTTPS port 443 only; strict ASCII normalized exact host allowlist. No wildcard
  subdomains, IP literals, credentials, query strings, fragments or ambiguous
  separators. Unicode hosts deliberately unsupported. URL paths can still carry
  private information: operator approval must restrict inputs to public pages.
- Resolve all DNS answers, reject if **any** is non-global, multicast, reserved,
  mapped/transition IPv6, metadata/link-local/private/loopback. Connect to one
  validated IP using urllib3 HTTPSConnectionPool; original hostname is explicit
  SNI, Host and certificate verification identity. No hostname re-resolution
  during connect, no TLS verification bypass, no proxy inherited from environment.
- No redirects, automatic retries, cookies, user headers, compression, JS,
  browser navigation, authenticated pages, CAPTCHA or anti-bot evasion.
- Operator must review site terms/robots and explicitly attest permission before
  enabling each domain. Automated robots.txt enforcement is **not implemented**.
  Refusals/rate limits return failure without retry. No child resources fetched.
- Maximum 5 sources, 256 KiB per response, HTML/plain UTF-8 only. Script/style,
  template/noscript/SVG data removed. Malformed HTML may yield conservative text
  loss. Content extraction is not a full browser or semantic document parser.
- Connect/read timeouts plus monotonic body deadline, remaining socket timeout
  updated per read. OS DNS resolution itself is not interruptible here; a stalled
  system resolver can exceed the application deadline. Bound the deployed Lambda
  timeout and DNS/egress network policy; no strict wall-time SLA is claimed.
- Gateway transport response cap 1 MiB, no retry or redirect. Default network
  timeout 30s and 45s streaming checks; no process-wide hard deadline. MCP v2025-11-25
  stateless calls and single JSON/SSE result supported; no sessions/elicitation.
- Model source content is marked untrusted; deterministic tool permissions do
  not depend on the model. This does not prove immunity to semantic prompt
  injection. Citation checks prove known IDs/URLs/text digests, not truthfulness.
- Report UI renders plain escaped React text, not HTML/Markdown, link previews or
  images. Unknown citation IDs/URLs fail. HTML-looking text remains inert text.
- Tool errors and live entrypoint errors are generic, without URLs or credential
  details. No credentials in browser, export, env files or recorded prompts.
  Runtime IAM is fetched server-side from the approved execution-role chain only.
- Actual Gateway response request/trace headers are returned if present. Missing
  IDs remain empty; no fabricated ARN or AWS trace. An ID is not authenticated
  trace retrieval. Managed judge remains NOT_CONFIGURED and overall eval false.

## Explicit live enablement (not run)

Live smoke requires `--live --human-approval`, operator-owned manifest/config,
`human_approved=true`, exact target account/region, manifest digest, site-condition
approval and identical domain allowlists, one-model-call budget, exact source-call
count, token cap and positive USD ceiling. Runtime additionally requires explicit
ENABLE_APPROVED_LIVE=1 and deployment digests. Missing fields block before calls.

On explicit live execution only: verify STS identity, read both Gateways and exact
Gateway targets, require IAM authorizer/READY and target configuration digest;
verify model target is the qualified Bedrock Mantle provider with Gateway IAM
outbound auth and `/v1/messages`; verify tool is the qualified Lambda target.
No direct Bedrock SDK inference, first-party Anthropic/OpenAI or Nova fallback.
OpenAI via Bedrock is future work, not enabled in this Claude-only slice.

The USD field is an approval ceiling, **not billing metering**. Per-invocation call
and token bounds do not meter repeated Runtime invocations. Do not enable a public
production path until durable aggregate budget/idempotency controls and current
policy/revocation checks are wired. Gateway interceptor changes also need separate
review: target snapshot checks alone do not attest every Gateway policy setting.

## Deployment and acceptance gaps

1. Production live catalog provider factory, cached refresh, refreshed scoped
   grants and Registry-to-runtime approved binding compiler remain unwired.
2. Build/test Linux ARM64 artifact; upload versioned S3/ECR; approved existing VPC,
   constrained egress, DNS, least-privilege Runtime and Lambda roles and Gateway
   targets. Restrict Lambda invocation to the approved Tool Gateway. No infra
   deployment or cloud mutation in this slice.
3. Existing SQS lifecycle needs live deployment continuation, immutable Runtime
   version association, live invoke, stale/revoke checks and durable cost controls.
4. Authenticated managed judge, dataset runner and actual trace retrieval/storage
   remain absent. Missing evidence cannot produce PASS or production-ready.
5. Perform explicit approved paid acceptance with both real Gateways, site policy,
   negative auth/revoke/stale cases, then independent peer review of the new SHA.

## Well-Architected / security-first source review

| Pillar | Source status / remaining work |
|---|---|
| Security | Fail-closed transport/input boundaries; deployed IAM/VPC, current grants, gateway policy and Lambda resource policy not validated |
| Reliability | Bounded fetch and generic errors, no fallback; durable live orchestration and real failure recovery missing |
| Performance | Five-source/size/token bounds; sequential fetching, real latency unmeasured |
| Cost | Single model call and token bounds; aggregate metering and real pricing enforcement missing |
| Operational excellence | Reproducible offline tests/source ZIP and real response IDs; cloud observability/judge/rollout not accepted |
| Sustainability | Small bounded work/no retry loops; deployment utilization unmeasured |

## Verified API references (2026-09-11)

- AWS `gateway-target-inference-provider.html`: `/inference/v1/messages`,
  target-qualified `model`, `bedrock-mantle.{region}.api.aws`, IAM SigV4 service
  `bedrock-agentcore`, Gateway IAM outbound auth.
- AWS `gateway-using-mcp-call.html`: `/mcp`, MCP-Protocol-Version 2025-11-25,
  JSON-RPC `tools/call`, target-qualified tool names.
- Installed botocore 1.43.92 control-plane shapes inspected offline.
- AgentCore Python SDK 1.22.0 application construction checked offline; urllib3
  2.7.0 pinned-IP/SNI/hostname verification interface inspected and unit-tested.

## Source verification, 2026-09-11

- Full offline pytest: **356 passed** (248 existing + 108 research/fetch cases).
- Local Playwright browser regressions: **8 passed**; hosted identity cases use
  intercepted synthetic APIs, not Cognito acceptance.
- Frontend TypeScript check and Vite production build: passed; existing large
  bundle warning remains. Build before backend tests: rebuilding concurrently
  briefly removes `dist/assets` and can race backend static-file setup.
- Actual harness smoke: one Tool Gateway MCP adapter call followed by one Model
  Gateway adapter call using mock transport; source, prompt, question, skill and
  report format propagate. No real Gateway acceptance is claimed.
- AgentCore SDK application construction succeeded offline. The runtime lock
  reproduces from `uv.lock` (only export-command comment differs). Linux ARM64
  native wheel installation/container build remains **unverified**, not bundled.
- Export uses an exact reviewed file allowlist, refuses symlinks and overwrite,
  and includes disabled execution config, Dockerfile and these limitations.
  It must not sweep future operator files, credentials or logs into an archive.
- Added rejection of empty ports/malformed percent escapes, strict boolean
  component approval, public IPv6 connection pinning and malformed report cases.
- No cloud reads/writes, paid inference, deployment, account migration, existing
  user/site changes or live catalog enablement were performed.
