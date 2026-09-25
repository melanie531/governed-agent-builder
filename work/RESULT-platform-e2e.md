# RESULT UNIFY+PLATFORM — 2026-09-25

**Status: STEP 2 + STEP 3 PASSED.** The governed platform (backend + Studio) is deployed in
AWS 534409838809 / us-west-2. The Path A Studio E2E passed through that deployed platform:
- A synthetic business user built and ran an Agent. The call went Agent Runtime → platform Gateway →
  specialist MCP Runtime.
- An admin then revoked the grant in the Studio UI, and the next run was denied.

**The specialist's underlying data is SIMULATED** (synthetic fixtures, `EXECUTION_MODE=local`).
The `snowflake:open_incident_counts` source named in its answer is a local fixture. No Snowflake is
called, and this is not a real-Snowflake or 3LO acceptance.

## SHAs (tested-code vs deployed vs report)
| What | SHA | Tests |
|---|---|---|
| Unified code (STEP 1, prior) | `adbdee3` | 1779 passed, 4 skipped (prior run) |
| Deploy-A cherry-pick (specialist Runtime scaffolding) | `6bbdf7d` | (covered by the final run) |
| **Final deploy SHA** (Tavily optional, specialist Gateway target, catalog data, Path A E2E script) | **`ce5b59e5f9497bfcefe139daee8b41b5770e5299`** | **1787 passed, 4 skipped, 0 failed** (513 s) (fresh full suite on this exact commit, clean tree: `uv run --locked --extra foundation-runtime --with "mcp>=1.30,<2" pytest -q`) |
| This report | the commit that adds this file (see `git log -1 -- work/RESULT-platform-e2e.md`) | docs only |

What was deployed from which SHA:
- **Lambda API + worker package:** sha256 `f9569c7923d5c30c0f7f2d8c4c4d1616fbbe9e015f17c85b0bb9eabf318b2f38`,
  built from `6bbdf7d` content plus the baked QA enrollments. The package includes
  `backend/ foundation_harness/ foundations/ tools/ runtime/`, 4 scripts and `uv.lock`, and
  `ce5b59e` changes none of those paths. Everything `ce5b59e` changed (the `journey_platform.py`,
  `infra/journey.py` and catalog data) is deploy-time input; it was applied from exactly that content.
- **Specialist MCP image:** built from `6bbdf7d88fe273ff7850d59ffc79ba3c21f31463`; `runtime/` is identical in `ce5b59e`.
- `examples/journey/templates.json`: the prompt was tightened after E2E attempt 1 (see below).
  It was republished with `journey_platform.py prepare` before the passing run, and the published
  content equals `ce5b59e`.

## STEP 2 — deployment (live)
- **Platform URL:** https://d3n4we1zdvik4w.cloudfront.net (Studio). CloudFront `E3CUD92PQXOWT3`.
  API Gateway `https://ab8y5pldz6.execute-api.us-west-2.amazonaws.com` sits behind the Cognito
  session and CSRF/same-origin checks. Invite-only Cognito pool `us-west-2_hJS60N7iq`.
- **Stacks:**
  - `governed-agent-builder-serverless-artifacts` (CREATE_COMPLETE)
  - `governed-agent-builder-serverless-app` (UPDATE_COMPLETE)
  - `governed-agent-builder-journey` (CREATE_COMPLETE)
- **Deploy path:** `scripts/serverless_deploy.py` (preflight/artifacts/deploy), then
  `scripts/journey_qa_identity.py` (business + admin, synthetic `@example.com`, credentials in SSM
  SecureString, enrollment expires 2026-09-26T14:30Z), then `scripts/serverless_package.py --qa-enrollments`
  plus redeploy, then `scripts/journey_platform.py prepare --specialist-runtime-arn … / activate / publish-ui`.
  Profile `default` is the instance role; STS was asserted as 534409838809.
- **Tavily is optional:** `TAVILY_API_KEY` is unset. `prepare` prints
  `TAVILY_API_KEY not set: Tavily web tools and the Research template are unavailable`.
  No Tavily target is created and no web search is mocked. The Research template is published
  `approved=false` because its tools are not all published.
- **Platform tool Gateway:** `gab-journey-tools-6fqwus6szi`, inbound AWS_IAM, endpoint
  **`https://gab-journey-tools-6fqwus6szi.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp`**.
  Targets:
  - `knowledge` (Lambda) = `THJBXHWHSA`
  - **`risk-analyst-specialist` (mcpServer) = `OTJHJ8YKJP`**, outbound `GATEWAY_IAM_ROLE` +
    `iamCredentialProvider{service: bedrock-agentcore, region: us-west-2}` (SigV4), endpoint
    `https://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-west-2%3A534409838809%3Aruntime%2Fgoverned_mcp_specialist-lStD9KFrE5/invocations?qualifier=DEFAULT`.

  Discovered `['knowledge___search', 'risk-analyst-specialist___agent-risk-analyst']`, and both probe
  invocations were verified. Foundation approval validated the Runtime target: same account and
  region Runtime URL, complete IAM credential provider, approved catalog record, and live
  discovery schema match.
- **Least privilege:** the Journey GatewayRole gets `bedrock-agentcore:InvokeAgentRuntime` only on the
  registered specialist runtime ARN (and its `/*`). It has no Runtime or API-key access by default,
  as pinned by `tests/test_gap_specialist_mcp_target.py`.
- **Specialist MCP Runtime (rebuilt):**
  - **Runtime ARN:** **`arn:aws:bedrock-agentcore:us-west-2:534409838809:runtime/governed_mcp_specialist-lStD9KFrE5`**,
    version 3, qualifier DEFAULT.
  - The no-data variant `governed_mcp_specialist_nodata-OxYEMkCsmY` (v2) was rebuilt alongside it.
  - Image: `534409838809.dkr.ecr.us-west-2.amazonaws.com/governed-agent/mcp-specialist:20260925T142234Z`,
    digest `sha256:6aaa9f7fc344a9bc00f5e67b3015fa303aa41c35e9408bce723596f29a5c0779`.

### Freshness (all later than build time 2026-09-25T14:22:34Z)
| # | Timestamp | Value |
|---|---|---|
| 1 | ECR imagePushedAt | 2026-09-25T14:22:51.285Z |
| 2 | Runtime lastUpdatedAt | 2026-09-25T14:22:55.506Z |
| 3 | Cold start (CloudWatch `/aws/bedrock-agentcore/runtimes/governed_mcp_specialist-lStD9KFrE5-DEFAULT`) | 2026-09-25T14:22:59Z: `governed-specialist-mcp cold start image_build=2026-09-25T14:22:34Z caller_scope_profile=full` |

The previous image's cold start at 14:21:17Z logged `image_build=2026-09-25T13:36:09Z`; the
new container reports the new build.

## STEP 3 — Studio E2E Path A (PASSED)
- **Runner:** `frontend/journey-e2e/path-a-live.mjs`, invoked as
  `GAB_RELEASE_STATE=<state> GAB_EXPECTED_ACCOUNT=534409838809 node frontend/journey-e2e/path-a-live.mjs`.
- It drives the hosted Studio with real Cognito sign-in and no intercepted responses. Credentials
  stay in memory only, and no auth trace or storage state is written.
- **Passing run** (attempt 6, 2026-09-25T15:04:53Z to 15:06:21Z):
  1. Admin (synthetic) signed in. The grant was restored, having been left revoked by attempt 5.
  2. Business user `68114350-…` (synthetic) signed in. Create agent → template **"Risk review
     (specialist)"** (platform-curated, pins `specialist-risk-analyst` / MCP server
     `mcp-risk-analyst-specialist`) → model Claude Haiku 4.5 → Deploy to AgentCore.
  3. Agent `84fd318d6f8d408992b07aad7ee3a889` went QUEUED → WAIT_RUNTIME → SMOKE → DEPLOYED. Its
     business Agent Runtime is `arn:aws:bedrock-agentcore:us-west-2:534409838809:runtime/gab_journey_f06cc83377830d73419b71a5-stcakC9Q4O`.
  4. **ALLOWED** at 15:06:17Z: "Run agent" in Studio. Job `5436abcdd3574f33b95c244d03dd8d1f` SUCCEEDED,
     `trace_id e9e56d8afb15f535d97625fb4d06563e`, `tool_calls=[risk-analyst-specialist___agent-risk-analyst {"question":"What is the renewal risk for our synthetic account?"}]`.
     Output (abridged): "**Renewal Risk: Elevated** … two open incidents and one delayed renewal …
     *Note: This data is simulated (synthetic)*".
  5. **REVOKE** at 15:06:18Z: the admin used the Studio UI (Foundation library → Tool / skill registry →
     Identity grants), entered the reason "Path A E2E: revoke specialist access for synthetic business
     QA user", and unchecked "<business QA> access to Ask the risk analyst specialist (simulated data)".
     `POST /api/admin/grants` returned 200.
  6. **DENIED** at 15:06:21Z: the business user reloaded the same deployed Agent.
     - The Studio shows "Review catalog capabilities: A selected capability is not approved for
       execution in your workspace", and **Run agent is disabled**.
     - The identical invoke request, sent from the Studio page (same-origin + CSRF, as the UI's `api()`
       does), returned **HTTP 403 `{"detail":"A selected capability is not approved for execution in your workspace"}`**.
- **Redacted trace** for the allowed call, from CloudWatch `/governed-agent-builder/journey/traces`,
  stream `agent-84fd318d6f8d408992b07aad7ee3a889-v1`, trace `e9e56d8afb15f535d97625fb4d06563e`:
  ```
  invoke_agent "Path A risk review …"           span f4c71c02562f3cbf  (root)
  ├─ chat global.anthropic.claude-haiku-4-5-20251001-v1:0   span 50c20d77…  in=741 out=71 tokens
  ├─ execute_tool risk-analyst-specialist___agent-risk-analyst   span f8d80a90…  ~0.87 s
  │    arguments: {"question": "What is the renewal risk for our synthetic account?"}
  │    result:    {"agent":"synthetic-risk-analyst","agent_version":"1","via":"mcp-tool-via-gateway",
  │                "steps":[gather_signals: "Two open synthetic incidents and one delayed synthetic renewal.",
  │                         weigh_signals: "…meet the synthetic elevated-risk rule.", recommend: "Schedule an account review…"],
  │                "inner_calls":[{"tool":"snowflake-approved-views","identity":"synthetic-svc-risk-analyst",
  │                                "source":"snowflake:open_incident_counts"}],   <- SIMULATED fixture, no Snowflake call
  │                "answer":"Synthetic risk review: elevated. Schedule an account review before the renewal date."}
  └─ chat global.anthropic.claude-haiku-4-5-20251001-v1:0   span 2d00d883…  in=1010 out=97 tokens
  ```
  The only tool path from a Journey Agent Runtime is the Gateway MCP client, and the tool name
  `risk-analyst-specialist___…` is the Gateway's target prefix. The result body is produced by the
  specialist Runtime's server.
- **Evidence files** (local, gitignored, mode 0600): `artifacts/journey-path-a/path-a-evidence.json`, plus screenshots
  `configure.png`, `allowed.png`, `revoked.png` and `denied.png`.

### Attempts before the pass (all real, nothing hidden)
1. Agent `fee1c33a…`: DEPLOYED and the run SUCCEEDED, but **no tool call**. Haiku asked for an account id
   instead of calling the specialist (trace shows no `execute_tool` span). Fix: the template prompt now
   says to call the tool immediately and not ask for an account id; republished via `prepare`.
2. Agent `d1eeca4e…`: the allowed call PASSED (trace `f35362768f2733203fc6d00375e5d561`).
   The revoke POST was sent, but Playwright `uncheck()` failed because the Cloudscape checkbox is
   controlled and only flips after the POST and catalog reload. Fix: `click()`, wait for the POST,
   then poll the state.
3. Agent `8643f404…`: the allowed call PASSED (trace `6a8a9f20cccce46d427f707a9c0d2a6b`). The Foundation
   library link matched both the side nav and the breadcrumb (strict-mode violation). Fix: navigate
   only when the tab isn't visible.
4. Agent `941a4672…`: allowed PASSED (trace `22f7d7ab2d4027e8001e049c6839e855`) and revoke PASSED. The
   Studio then **disabled Run agent** (readiness re-check), so no POST was sent and the test timed out
   waiting for one. Fix: assert the UI block, then send the invoke request.
5. Agent `08f54ee2…`: allowed and revoke PASSED. The raw Playwright API POST got
   `403 Same-origin request required`: the CSRF/origin guard worked, but this is not the grant denial.
   Fix: send the request from inside the Studio page.
6. Agent `84fd318d…`: **full PASS** (above).

## Teardown (NOT executed; resources left running for review)
Delete in order. The six `gab_journey_*` runtimes are one per E2E attempt (created 14:43Z–15:05Z). Studio agents first, preferably via Studio "Delete agent", which also removes the
Runtime, workload identity and S3 evidence. Or:
```
for id in gab_journey_f06cc83377830d73419b71a5-stcakC9Q4O gab_journey_c768ef951b34e4324bc75f58-ntmSnYE1VZ \
          gab_journey_ab35fb5df5f4a80ea4e1339e-fStsOs7H5w gab_journey_5b085e8db022a7608a6a8ad7-apTzHD8lAj \
          gab_journey_45a35cef3692624afb6808db-4Kr9KY94QZ gab_journey_3f6a9725f46df6145b9d8fa4-c6VsBx8XFf; do
  aws bedrock-agentcore-control delete-agent-runtime --region us-west-2 --agent-runtime-id $id; done
# Journey Gateway + targets (created by journey_platform.py, not by the stack)
aws bedrock-agentcore-control delete-gateway-target --region us-west-2 --gateway-identifier gab-journey-tools-6fqwus6szi --target-id OTJHJ8YKJP
aws bedrock-agentcore-control delete-gateway-target --region us-west-2 --gateway-identifier gab-journey-tools-6fqwus6szi --target-id THJBXHWHSA
aws bedrock-agentcore-control delete-gateway --region us-west-2 --gateway-identifier gab-journey-tools-6fqwus6szi
aws cloudformation delete-stack --region us-west-2 --stack-name governed-agent-builder-journey   # empty its evidence bucket first
aws cloudformation delete-stack --region us-west-2 --stack-name governed-agent-builder-serverless-app   # empty web bucket; Cognito/table retention per docs/HOSTED.md
aws cloudformation delete-stack --region us-west-2 --stack-name governed-agent-builder-serverless-artifacts   # empty releases bucket (versions) first
# QA identities: SSM SecureStrings under /governed-agent-builder/journey-qa/ (deleted with the pool or explicitly)
# Specialist Runtime + standalone Deploy-A gateway: the exact commands are in work/RESULT-deploy-A.md "Teardown"
# (gateway governed-mcp-specialist-gw-3glptxtem4, runtimes governed_mcp_specialist[-_nodata], roles, ECR repo, log groups).
# Delete the specialist Runtimes only after the Journey Gateway target OTJHJ8YKJP is gone.
```
The standalone Deploy-A gateway `governed-mcp-specialist-gw-3glptxtem4` was **kept**. It is not on the
Path A platform route, but its targets point at the same rebuilt Runtimes and remain a working
direct-acceptance harness (`scripts/mcp_gateway_acceptance.py`).

## Cost note
- Everything is request- or storage-billed, with no new always-on compute or NAT:
  - Lambda, API Gateway (HTTP), DynamoDB on-demand, SQS, CloudFront/S3, Cognito (a few users) and
    CloudWatch Logs: cents per day at this traffic.
  - AgentCore Gateway: per-request.
  - AgentCore Runtime: billed on active session CPU/memory consumption; idle runtimes with no
    sessions should not accrue compute.
  - ECR: storage for the specialist image tags, a few cents per month at most.
- Bedrock Haiku 4.5 tokens for the whole E2E (6 attempts plus smoke/probes) are a few tens of
  thousands of tokens, under USD 0.10.
- Observation to check: the specialist Runtime log shows `POST /mcp 200` about once per second
  from two local connections, continuing after the E2E. I did not determine whether this is
  AgentCore health or session keep-alive traffic or a lingering session. If it is a live session,
  Runtime memory is billed while it lasts. Deleting the runtimes (above) stops it.

## History

### STEP 1 — unified commit (history)
- Clone: `/home/ec2-user/work/gab-unify`, branch `feat/tool-agent-as-tool-and-builtin-snowflake`
- **Unified SHA: `adbdee3ff95dd5ac364c8f05c379bcf6f238c563`** (merge; parents `2b62acf`, `0f319783`),
  author `Melanie Li <lpy1222@gmail.com>`
- `git log origin/main..HEAD`: 7 commits (3b1b745, e443411, 79d3f64, 417ddb7, 2b62acf, 0f31978, adbdee3).
  This uses the local `origin/main` ref. It could not be refreshed because fetching also needs credentials.

### How the `backend/foundation_approval.py` conflict was resolved (both capabilities kept)
- **From 2b62acf (catalog targets):** an mcpServer Gateway target must exactly match an approved,
  non-placeholder catalog `tool_target` record: same endpoint, same outbound auth, and tool
  names prefixed `<target>___`. Otherwise it fails with `APPROVED_MCP_SERVER_TARGET_REQUIRED`.
- **From 0f31978 (runtime targets):** a GATEWAY_IAM_ROLE mcpServer target must be this
  account's same-region AgentCore Runtime data-plane URL (`RUNTIME_MCP_SERVER_ENDPOINT_REQUIRED`).
  It must also have a complete `iamCredentialProvider{service: bedrock-agentcore}`
  (`IAM_SIGV4_CREDENTIAL_PROVIDER_REQUIRED`).
- **Live discovery (from 0f31978), now required for every mcpServer target:** live Gateway
  discovery must expose exactly the approved tool names and schemas
  (`TOOL_TARGET_DISCOVERY_REQUIRED` or `TOOL_TARGET_SCHEMA_REQUIRED`).
- **Catalog shape:** `tool_target` accepts the Runtime URL (including `?qualifier=`) only for
  GATEWAY_IAM_ROLE records. It rejects IAM outbound auth on any other endpoint.
- **Unknown target shapes** fail closed with `TOOL_TARGET_SCHEMA_REQUIRED` on both paths. One
  2b62acf test expectation was changed to this code.
- **Test updates:**
  - 0f31978's gap tests now pass an approved catalog record.
  - New test: a correct Runtime-B target with no approved record, a placeholder record, or a
    drifted record is rejected.
  - `test_mcp_server_targets` covers: discovery required, discovery mismatch, Runtime IAM
    records, and IAM outbound auth on a non-Runtime endpoint.

### WIP salvage (`/home/ec2-user/work/gab-integration-20260925`)
That clone held an unfinished version of this same merge:
- 13 of its 15 files are byte-identical to 2b62acf.
- Its `foundation_approval.py` calls `verify_tool_target(...)` with 6 arguments, but the function
  still takes 4, so it is broken.
- Its one real idea (live-discovery schema matching) is included in the resolution above.

Nothing else was uncovered.

### Tests (with `frontend/node_modules` present, copied from gab-didi-review; identical package-lock)
- `uv run --locked --extra foundation-runtime --with "mcp>=1.30,<2" pytest -q`: **1779 passed,
  4 skipped, 0 failed**. The 4 skips are the DynamoDB-CAS-only tests in `test_diagnostic_exchange.py`.
- An earlier run in a venv without `mcp` also passed (1766 passed, 5 skipped). That run skipped the
  MCP server tests, so the `uv` run above is the authoritative one.

#### Earlier push blocker (resolved: parent pushed `adbdee3` with the SSM PAT)
Step: `git push origin feat/tool-agent-as-tool-and-builtin-snowflake`
- With no credential helper: `fatal: could not read Username for 'https://github.com': No such device or address`
- With `GH_TOKEN`/`GITHUB_TOKEN` from the environment (same token) as the password:
  `remote: Write access to repository not granted.` and `fatal: ... The requested URL returned error: 403`
- GitHub API `GET /repos/melanie531/governed-agent-builder` with that token: `404 Not Found`. The
  GitHub MCP tool also returns `Not Found`, so the token cannot see this private repository.
- Earlier pushes (3b1b745) used a PAT embedded in the remote URL. That PAT has since been removed
  from `.git/config`. I did not search for other credentials.

**To unblock:** provide a credential with write access to `melanie531/governed-agent-builder`
(for example, a fine-grained PAT with Contents: write in `GH_TOKEN`). Then run:
`git -C /home/ec2-user/work/gab-unify push origin feat/tool-agent-as-tool-and-builtin-snowflake`
and check that the remote branch SHA equals `adbdee3`.

### STEP 1 findings for STEP 2/3 (pre-work, superseded by the sections above)
- **The platform does not exist in 534409838809.** There are no `governed-agent-builder-*` stacks.
  The existing Studio (`de32ssfw7gsad.cloudfront.net`) is under profile `agentic-platform-prod`,
  which is not this account. STEP 2 is therefore a fresh deploy:
  `scripts/serverless_deploy.py` (preflight/artifacts/deploy/publish), then the Journey layer
  (`scripts/journey_platform.py prepare/activate/publish-ui`, stack `governed-agent-builder-journey`).
  The Journey layer is the path where a Studio-built agent runs on an AgentCore Runtime and calls
  tools through the `gab-journey-tools` Gateway.
- **Journey `prepare` needs `TAVILY_API_KEY`.** It is not set on this host, and no Tavily secret or
  provider exists in the account. Code changes needed for Path A:
  1. Make the Tavily target optional.
  2. Add a `risk-analyst-specialist` mcpServer target (Runtime URL, GATEWAY_IAM_ROLE +
     iamCredentialProvider) to the Journey Gateway, plus entries in
     `examples/journey/mcp-servers.json` and `tool-publications.json`.
  3. Grant the Journey GatewayRole `bedrock-agentcore:InvokeAgentRuntime` on the specialist
     runtime ARNs. This flips the gap pin `test_gateway_role_lacks_invoke_agent_runtime_today`,
     as that test anticipates.
- **Models work in this account.** Bedrock Converse returns content for
  `global.anthropic.claude-haiku-4-5-20251001-v1:0`, and `global.openai.gpt-6-astra` is reachable.
- **The specialist MCP Runtime and Gateway (Deploy-A) are already live in this account.** They
  were built from `d2dcdb0` (2b62acf + Deploy-A), not from the unified SHA; see
  `/home/ec2-user/work/governed-agent-builder/work/RESULT-deploy-A.md`. Deploy-A's files were
  also copied into this clone untracked at 13:43; they are byte-identical to `d2dcdb0`.
  Rebasing means cherry-picking `d2dcdb0` onto `adbdee3` and rebuilding the image.
  The underlying specialist data is **simulated** (synthetic fixtures, `EXECUTION_MODE=local`).
- **Studio E2E needs a synthetic QA sign-in.** It would use `scripts/journey_qa_identity.py`
  (synthetic `@example.com` Cognito users, credentials in SSM, 24h enrollment baked into the
  package). The Playwright runner `frontend/journey-e2e/live.mjs` is pinned to account
  820242898417 / profile `account-820` and would need retargeting.
- **Revocation denial is a real platform check.** The Journey invoke path re-checks catalog grants
  on every call (`backend/journey.py` action → validate → `catalog.resolve`), so an admin revoking
  the specialist grant blocks it there. Caveat: the native Runtime API path does not re-check
  grants.

