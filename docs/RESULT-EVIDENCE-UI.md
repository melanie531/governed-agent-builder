# Result evidence UI (source-only)

## Delivered contract

`backend/result_evidence.py` reads a bounded, versioned evidence object and projects only Report, Evaluation, Execution and Cost. The existing authenticated `GET /api/jobs/{job_id}` is the sole detail/replay endpoint. Agent ownership and workspace checks apply to all callers, including administrators. Agent/job lists do not contain report text. Live event details are reduced to stage/mode; stored inputs, raw SDK responses and provider exceptions are not exposed. No console URLs, runtime URLs, raw-log links or presigned S3 links are accepted or generated.

The Cloudscape detail page branches between existing fixture results and live evidence. Live results never fall back to fixtures. Immutable result replay selects an existing job/version without invoking anything; its authenticated JSON link uses the existing endpoint. Current-version execution controls remain unchanged. Plain React text nodes render reports and citations (no HTML/Markdown interpretation or remote images).

## Current collector boundary

The disabled-by-default collector in [EVALUATION-COLLECTOR.md](EVALUATION-COLLECTOR.md) implements native Evaluate request/response persistence, bounded pinned span reads, immutable receipts and descriptor registration. `AgentCoreEvidenceRepository` is a concrete scoped S3 reader. `FoundationJobs.step(EVALUATING)` invokes the collector and then the reader. Neither constructs SDK clients. An explicitly injected code adapter supports offline deterministic checks; the production factory does not supply it. The actual complete-span exporter/grant writer, native event correlation and deployed service authentication remain unverified, not fabricated.

An authorized server-side collector must persist `foundation-run:<job>.evidence_source`, never accept it from Runtime or user request payloads. Descriptor fields:

- `binding`: exact run_ref, owner, workspace, agent, immutable version, definition/manifest digest, epoch, runtime ARN/version, policy version, dataset/rubric digests.
- `key`, `version_id`, `sha256`: exact S3 object in the reader's fixed bucket/prefix. Object metadata `binding-digest` must equal the canonical binding digest. Maximum read 64 KiB, stream always closed.
- `source_ids`: approved retrieved source identities, equal to the content source list. Citations reference these IDs, not arbitrary URLs.
- `query_id`: a previously started, allowlisted Logs Insights query. Returned records must each contain the exact binding digest and trace ID; only Complete nonpaginated bounded results are accepted. Trace IDs must match content and runtime response.
- `evaluations`: 1–20 server-owned receipt pins: id, receipt_id, version_id, sha256, receipt_digest, evaluator_id, evaluator_version (native snapshot digest), ordered case_ids and required case count. Each receipt binds the run/owner/workspace/runtime, dataset/rubric, pipeline_run_id and AGENTCORE_EVALUATE_V1 pipeline. Cases include request_id, case_id, trace_id/span_id, persisted Evaluate request/response and collector rubric PASS/FAIL. Native errors, absent results, mismatched evaluator/span/context, partial coverage and summary disagreement fail closed. Receipt objects are bounded to 64 KiB each. These are application contract fields, not a fabricated AWS response schema.

Fetched JSON fields: binding, report `{text,citations:[{id,title,source_id}]}`, source_ids, trace_ids, evaluations `[{id,status,completed,required,judge_complete}]`, optional provider `{route,route_version,usage}`. Unresolved/duplicate/unknown-source citations are quality failures: safe bound report text remains visible, citation entries are omitted and citation_status is INVALID. Unknown report/citation fields are rejected. Unknown top-level evidence fields are rejected, not projected. `readback` and inline receipt claims in fetched content cannot grant authority: receipts are fetched separately from the repository, and readback is set only after checks. Projection/replay rechecks receipts against the server-owned descriptor; Runtime success or a normalized summary alone is blocked. Injected evidence readers are trusted server-side dependencies, not public data entrypoints.

Missing/malformed content, wrong bindings, incomplete traces/coverage and SDK failures yield explicit BLOCKED with report absent. A completed evaluation with FAIL can display its verified report but cannot produce LIVE_PASS. Deterministic results are AVAILABLE with SEMANTIC_JUDGE_NOT_RUN, quality INCOMPLETE (or FAIL) and required_judge_passed=false. Display availability is never release PASS. Citation defects yield quality FAIL and INVALID_CITATIONS while React text nodes safely escape the report; invalid references are not trusted links. Existing settlement, Linux artifact and execution gates still apply.

## Cost semantics

Only provider usage inside verified evidence is considered, never Runtime ready flags or reservation balances. `Usage` strictly validates input/output and optional cached_read/cached_write token counts. Approved server configuration may supply `model_rate_schedule`: route, route_version, version, source, date (YYYY-MM-DD), currency, token_basis=`exclusive`, per_million rates keyed by token dimensions. Usage and rate dimensions must match exactly; no silent missing-cache pricing. Cached token normalization must be performed by the trusted collector according to the actual provider protocol.

Decimal arithmetic produces an **estimated model inference cost**, not an invoice or all-service total. Missing usage/rates or route-version mismatches yield UNKNOWN/null, not fabricated zero. Runtime, Browser, tools, storage, telemetry and evaluation remain unallocated. This display neither releases reservations nor changes the existing all-service admission cost envelope.

## Offline verification

Synthetic SDK responses exist only in tests. `tests/test_result_evidence.py` covers mismatched owner/workspace/runtime/version/policy/dataset/rubric, trace mismatch, judge/coverage gaps, unknown citations, forbidden URL fields, malformed/provider errors, unknown pricing and cached pricing, plus owner-scoped API projection and non-owner/admin denial. Existing wiring tests simulate trusted receipt registration using synthetic fixtures only. Regressions reject generic Bedrock job clients, missing pipeline bindings and Runtime self-asserted success. This correction uses only scoped offline Python tests; it does not rerun browser/build or claim cloud acceptance.

Commands (installed dependencies only):

```
.venv/bin/python -m pytest -q tests/test_result_evidence.py tests/test_foundation_wiring.py tests/test_authorization.py tests/test_workflow.py
npm --prefix frontend run build
.venv/bin/python -c 'from tests.test_result_evidence import sample; from backend.result_evidence import project; import json; r,e=sample(); open("work/result-evidence-browser-fixture.json","w").write(json.dumps(project(r,e)))'
node frontend/e2e/result-evidence-offline.mjs
```

The browser test serves the built UI on ephemeral loopback, mocks API responses with Python-projected synthetic evidence, rejects external requests, verifies escaped report HTML, evaluation/coverage UI, estimated cost, unallocated charges and authenticated replay link. This is NOT real-cloud end-to-end acceptance. Test logs/screenshot are under `work/result-evidence-*`.

## Unverified integrations and review

The native Evaluate collector, immutable receipt repository and injected offline deterministic adapter exist in source. They have not been deployed or live-accepted. Actual provider usage normalization and reviewed rate schedules remain unverified. Current telemetry does not itself emit this full binding/coverage contract; a reviewed server query/collector mapping is still needed. No trace/log drilldown endpoint exists, so UI explicitly says unavailable. Real S3/CloudWatch/AgentCore Evaluate permissions, privacy-reviewed report content, principal/session lifecycle and cloud readbacks are unverified. Owner binding does not itself redact sensitive generated report text; only authorized owner detail displays that output. Source/log views do not expose prompts.

Security: exact bindings/read-only allowlist; remaining live trust boundaries are the exporter/grant writer and deployed service authentication. Reliability: bounded reads, no retries, fail-closed. Performance: 64 KiB reports and capped evidence IDs; frontend bundle size warning remains. Cost: versioned inference-only estimate, unallocated services explicit. Operational excellence: reproducible offline tests and missing integration labels. Sustainability: no new provisioned resources or model calls.

No configuration, IAM Deny, deployment, login identity, Git index/commit/push or nightly status edits. peerQAidentity remains blocked. The pre-existing model-gate fault files are untouched.
