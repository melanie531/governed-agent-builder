# AgentCore evaluation collector (source-only)

## Implemented, not live validated

`backend/evaluation_collector.py` now implements synchronous native Evaluate, bounded complete-span retrieval, conditional/versioned S3 receipt writes and exact readback, and protected descriptor registration. `backend/result_evidence.py::AgentCoreEvidenceRepository` is now a concrete S3 repository, not an abstract missing collector. No client or evaluator is constructed by these classes. Existing injected fixture repositories and the public result-evidence-v1 projection remain compatible.

This increment has not enabled configuration, changed IAM, created an evaluator, provisioned storage, deployed code, or performed a cloud/model/network call. Tests use installed dependencies, SDK Stubber and Moto. No Git index/commit/push operations or fault-probe/UI edits were performed.

## Exact caller chain

1. Existing `configured_jobs(store, worker=True)` still requires `FOUNDATION_LIVE_ENABLED=1`. Only protected `foundation-deployment.evaluation_collector.enabled == true` activates the additional factory. Default is disabled. Explicit bucket, prefix, `span_contract=PINNED_COMPLETE_SPANS_V1`, and injected AgentCore data/control, S3 and Logs clients are mandatory. No ready flag or fixture fallback is introduced.
2. `FoundationJobs.step`, stage `EVALUATING`, calls `EvaluationCollector.collect(store, job_id)` before `EvidenceReader(row)`.
3. `context()` reloads the job/run, calls existing `runs.current` (current version, grant, policy, artifact, membership/session authority), requires EVALUATING, and reads immutable approved evaluator/source settings and a separate protected per-run export grant from protected backend state. Runtime response fields cannot supply them.
4. The existing `store.tx()` serialization/CAS commits a CLAIMED record **before** external operations. It binds the approved settings, exact run/runtime/version, evaluator source and input descriptor. A repeat of COMPLETE returns only the committed descriptor. CLAIMED/uncertain attempts never repeat Evaluate; the native API has no idempotency token. A crash/failed CAS holds the claim and existing reservations for operator reconciliation.
5. The collector verifies versioning and all four S3 Public Access Block settings, then calls `control.get_evaluator(evaluatorId=...)`. The normalized whole native snapshot (including level/status/config/updatedAt) must equal the approved snapshot and source record. No evaluator creation or modification exists.
6. `PinnedSpanReader` calls `s3.get_object(Bucket, Key, VersionId)` for the protected complete-span export and `logs.get_query_results(queryId=...)` for its exact authorized span query. It requires complete/nontruncated export, exact source text digest and source identities, ordered expected span identities, and complete query coverage. Reads are at most 65,537 bytes with streams closed; accepted objects are at most 64 KiB. At most 100 spans and 20 evaluation cases are supported. Larger data fails closed, not sampled.
7. Native request serialization uses only installed SDK fields: `evaluatorId`, `evaluationInput: {sessionSpans: actual_spans}`, `evaluationTarget: {traceIds: [trace_id]}` for TRACE or `{spanIds: [span_id]}` for TOOL_CALL. `level` is read from GetEvaluator, **not** invented as an Evaluate request field. All complete spans are retained. Report quality must use TRACE. SESSION is explicitly unsupported in this bounded increment.
8. `agentcore.evaluate(**request)` runs once per approved case. Native request and response (including ResponseMetadata/request ID) are persisted, including returned native errors and caught SDK ClientError responses. Receipt normalization requires exactly one result for the single target, matching evaluator ID/ARN/name, session/trace (and span for TOOL_CALL), no native error/ignored reference fields, and a real finite score or allowed label. The approved numeric/categorical rule computes PASS/FAIL. Completion alone never means PASS. Partial/duplicate results fail closed.
9. Canonical receipt includes native config digest, level, full raw request/response, request ID, case identity, exact input pin, approval digest, pipeline ID and run/owner/workspace/runtime/version/policy/dataset/rubric provenance. Immutable keys are `<fixed-prefix>/<binding-digest>/<kind>-<content-sha256>.json`. Writes use `IfNoneMatch='*'`, AES256 and application/json. Every write obtains a non-null S3 VersionId and reads back that exact version, metadata, byte length and SHA-256. S3 version deletion/retention protection still requires reviewed IAM/bucket policy; versioning alone is not Object Lock.
10. Report/evaluation/trace/provider projection is validated before descriptor registration. Missing provider usage or reviewed rates leave cost UNKNOWN/null; they do not discard valid report/evaluation descriptors or affect quality. Admission, held reservations and reconciliation remain independent. The final protected transaction rechecks authority and the original fence, and atomically commits `foundation-run:<job>.evidence_source` with COMPLETE. Failed commit leaves only unreferenced private objects; no evidence descriptor, release or LIVE_PASS. A native FAIL can produce a verified visible report but cannot pass the job.
11. `EvidenceReader` scopes the concrete repository to the protected row, reads only registered receipt/version grants and fixed job-specific prefix, rechecks digest/binding and existing trace query, then uses the unchanged report/version/trace/cost projection. Existing EVIDENCE_CHECK execution/Linux/settlement gates remain.

## Required protected records (not public request fields)

No actual native evaluator configuration/source record existed in the inspected repository. This implementation does **not** invent one. The existing approved artifact can carry a reviewed `agentcore_evaluation` entry without changing the strict runtime harness schema:

- `id`, `purpose` (`report_quality` for full report), `native`: exact JSON-normalized GetEvaluator snapshot. Datetimes use ISO-8601; ResponseMetadata is excluded from configuration digest only.
- `source_record_key`: `foundation-evaluator:<approved-id>`; `source_record_digest`: canonical digest of that existing protected source record. Its `native` must match exactly.
- `rubric_digest`, `dataset_digest`: equal the immutable approved harness refs.
- `case_ids`: ordered, unique approved evaluation case identities, without future runtime/trace pins.

The protected run field `collection_grant` contains `approval_digest=digest(row.approved)` (the immutable finalized approval), exact `binding`, `input_digest=digest(collection_input)`, and `cases` with case_id/trace_id/span_id/session_id. Ordered case IDs must equal the immutable approval. The input digest covers runtime/job binding, source digest, object/version/hash, span coverage and both query IDs. Only a trusted exporter/grant writer may establish it; no public writer is supplied. This never mutates `approved`, the artifact approval digest, or `runs.current`.
- `decision`: reviewed numeric `{kind, min, max, pass_min}` or categorical `{kind, labels, pass_labels}` scoring rule. No model-generated release rule is accepted.

`collection_input` is a **trusted exporter-written run field**, not Runtime payload:

- exact `binding`, `key`, `version_id`, `sha256`, optional fixed `bucket`;
- ordered `source_ids`, `source_digest`, ordered `span_ids: [[traceId, spanId], ...]` representing the independently expected complete export;
- `query_id` for the already authorized span query (binding_digest/trace_id/span_id only);
- `trace_query_id` for existing result reader's distinct-trace query (binding_digest/trace_id only).

The immutable export contains binding, `complete:true`, `truncated:false`, no continuation, `sessionSpans` in the actual supported telemetry format, report/citations, sources `[{id,text}]`, source_ids, trace_ids, and provider route/version/usage. It is protected content, not a public URL. A trusted exporter must establish completeness against independently known expected spans; an untrusted producer saying “complete” is insufficient. Missing exporter/record fails closed. This increment supplies the bounded query/read consumer, not a fabricated exporter success.

## One explicit custom-code integration gate

The installed botocore control model defines `codeBased.lambdaConfig.lambdaArn` and timeout. It does **not** specify the service-to-Lambda event payload or guarantee that arbitrary OTel attributes/private S3 references reach that Lambda. Evaluate has no `evidenceReference`, `report`, `sources`, or `level` top-level request fields.

An explicitly injected `CodeEvaluator` now registers protected evaluator+trace mappings in the collector claim transaction, reads the exact pinned content, checks an injected service-entry capability, and caches deterministic structural/reference results. This is an OFFLINE injection path only. `configured_jobs` does not supply that adapter, so codeBased stays blocked by default. A real exporter/grant writer, native event correlation and independently authenticated service entry/resource policy remain unverified; an event field or Lambda context is not caller authentication. No additional service adapter is supplied here.

For currently supported non-code TRACE evaluators, the collector requires actual report and source text to already occur in the **actual spans**; hash-only input is rejected. It does not manufacture spans, upload content to Logs, create presigned links, or publish raw evidence. Existing telemetry privacy/retention must be reviewed before sending any real content to Evaluate. Report/source text not already safely present fails closed. Private content retrieval for code evaluators is not live-ready.

## Offline tests and remaining operations

`tests/test_evaluation_collector.py` uses synthetic S3 objects under Moto, SDK Stubber request matching, and denied socket connect. Covers TRACE/TOOL_CALL shape, native fail/error/empty/duplicate/no-score/wrong IDs, SDK exceptions, bounded source reads, source/binding isolation, immutable version/hash/metadata, unknown usage, forged Runtime pins, idempotency, config drift, and rollback of failed final CAS. Existing result-evidence/wiring/auth/workflow tests cover fixture compatibility and owner-only projection. The final suite runs with socket connect and create_connection disabled across all tests.

Final scoped offline run: **201 passed**, 2 existing Starlette/httpx deprecation warnings, **16.66 seconds**. Includes direct EVALUATING collector-to-reader success and missing-input failure tests, plus the existing result-evidence/wiring/authorization/workflow suites. No live validation.

Remaining external work is limited to: approved existing native evaluator snapshot/source/rubric decision; trusted complete-span exporter/query pins for an actual run; fixed private versioned S3 configuration and scoped S3/GetEvaluator/Evaluate/Logs/KMS permissions as needed; the custom-code mapping gate above if using codeBased; reviewed provider usage/rates; authorized deployment and real readback/E2E acceptance. None is claimed done here. No credentials or execution permissions were changed.

Security: fixed grant/version/hash checks, private store, content not logged by collector. Reliability: CAS claims and no uncertain paid replay. Performance: bounded 64 KiB receipts/input and explicit caps. Cost: unknown pricing retained independently, no new paid execution in this increment. Operational excellence: synthetic reproducible tests and named integration gate. Sustainability: no resources provisioned.

## Independent review corrections (source only)

Owner content visibility is distinct from quality/release: authenticated, bound deterministic output is AVAILABLE with SEMANTIC_JUDGE_NOT_RUN and required_judge_passed=false. Bad citation references produce visible escaped report text, INVALID_CITATIONS, quality FAIL and no endorsed citation entries. Unsafe schema fields and unbound/forged evidence remain hidden. EVIDENCE_CHECK requires independent quality PASS and required judge success, execution, settlement and Linux evidence; known price is not a quality prerequisite.

An outer `foundation-evaluating:<job>` unique owner token plus observed stage/approval-grant-runtime fence owns the entire collect/read/transition. Duplicates return without clearing evidence or moving the stage. A collector CLAIMED result raises a distinct in-flight/uncertain signal; no paid Evaluate retry occurs. A crashed outer owner remains held for explicit reconciliation, not automatic lease takeover.

The previous 201-pass result above is historical. Current bounded verification and exact test inventory are in `work/custom-foundation/RESULT-EVAL-FIX-REPORT.md`.
