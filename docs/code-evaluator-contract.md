# Deterministic TRACE code evaluator contract

## Implemented, offline-tested boundary

`backend.code_evaluator.CodeEvaluator.handle(event, context)` is a concrete handler, not an abstract evaluator placeholder. It emits native `{label, value, explanation}` PASS/FAIL results or `{errorCode, errorMessage}` errors. It creates no SDK clients, invokes no model, and logs no event, prompt, source text or exception body.

The event contract supplied by the parent is `schemaVersion: "1.0"`, `evaluatorId`, `evaluatorName`, `evaluationLevel: "TRACE"`, `evaluationInput.sessionSpans`, `evaluationReferenceInputs`, and `evaluationTarget.traceIds`. Event identifiers are routing data, **not caller authentication**. `evaluationReferenceInputs` is ignored, including any URL, bucket, owner or version claims. Private content is never fetched from event references.

The constructor requires an injected protected metadata store and fixed S3 store location. `authenticate_service(context)` must return exactly `True`; absent or failing authentication denies invocation. This callback gets no event. The test uses an in-process service-entry capability, not a fabricated end-user token. No default cloud Lambda entry is enabled, and a Lambda context alone does not establish caller identity.

## Protected registration and retry behavior

The collector accepts codeBased only when explicitly injected with this concrete adapter, the same metadata and S3 stores, and the same fixed bucket/prefix. The approved configuration must pin `code_adapter: DETERMINISTIC_TRACE_V1`, a TRACE codeBased native snapshot, and exactly this decision:

```json
{"kind":"categorical","labels":["PASS","FAIL"],"pass_labels":["PASS"]}
```

Within the existing collector CLAIM transaction, before Evaluate, the adapter registers `foundation-code-evaluator:<digest(evaluatorId, traceId)>`. Each mapping fixes the job, full owner/workspace/version/runtime/policy/rubric/dataset binding, approved evaluator snapshot, input version/hash pin, immutable finalized approval digest, evaluator digest, collection fence, case and session. Duplicate trace registrations cannot rebind another job, version, evaluator or session. Ambiguous targets are denied.

The handler rechecks existing `runs.current` authority and collector context/fence both before reading and before result commit. Cached results also require current authority and identical span digest. The immutable mapping and result live in the existing transactional repository. Concurrent deterministic computations can read twice, but cannot disagree at commit; no model is ever called. A completed collector retry returns its committed descriptor without another Evaluate. Uncertain external Evaluate outcomes remain blocked, not automatically replayed.

## Content and checks

* Event canonical JSON is capped at 6 MiB; immutable content uses the stricter existing 64 KiB read limit, exact S3 VersionId, SHA-256 and binding metadata.
* One trace target, 1–100 unique well-formed trace/span pairs, exact registered coverage and exact stored span digest are required. This adapter deliberately rejects multi-trace span sets rather than guessing session/trace selection.
* Sources, source IDs/digest, completion and truncation flags, report text, and trace membership must match the protected export. Actual spans must contain report/source text. If a span includes sessionId it must match the registered session; absence does not manufacture a native session claim. Session authority comes from the protected exporter/case mapping.
* PASS requires a valid Report schema, nonempty report, citations used exactly once in the citation ID set, no duplicate citation IDs, bracket references matching that set, and citation source IDs present in the protected source set. Repeated references in prose are allowed. Structural/citation failures produce FAIL; missing/incomplete/unbound content produces a native error.
* These are reference/format checks, **not semantic entailment, factual accuracy or prompt-injection resistance**. The fixed adapter version and approved rubric digest bind the check implementation; arbitrary event rubrics are not executed.

## Collector and evidence semantics

The collector verifies native label/value against the handler's protected cached result before accepting a codeBased result. It persists ordinary native receipts and marks `judge_complete: false`. The reader derives this flag from the native codeBased configuration, not a caller flag. Collector, EvidenceReader and project accept integrity-verified, explicitly tagged deterministic receipts for owner display: AVAILABLE with SEMANTIC_JUDGE_NOT_RUN and required_judge_passed=false. Deterministic PASS is not semantic judge completion or release PASS. Safe bound reports with invalid citations remain visible as escaped text with quality FAIL/INVALID_CITATIONS; citation entries are omitted. Unsafe schema fields and forged provenance are rejected. Missing usage/rates remain UNKNOWN and do not discard quality evidence. Semantic judging remains unavailable in this adapter.

Defaults remain disabled. The production factory does not inject this code adapter. No live permissions, cloud resources or deployed website were changed.

## Remaining real service/IAM verification

Before cloud enablement, independently verify the exact AgentCore-to-Lambda invocation principal, source-account/source-resource restrictions where supported, function resource policy, execution-role least privilege, and how the deployment's trusted service-entry adapter establishes caller authenticity. Do not infer this from event fields or Lambda request ID.

Capture a sanitized real TRACE service event to verify actual span serialization/order, session representation, evaluationReferenceInputs shape, single-trace selection, service retry semantics, and native response label/value propagation. The strict exact-span comparison intentionally fails closed if the real service transforms spans. The event field contract here comes from parent verification; no cloud compatibility or IAM acceptance was performed in this task.

The protected SQLite test repository is not a production encryption/retention solution. Deployment must independently validate durable shared-store isolation/transactions, immutable S3 versioning and access controls. Required semantic judging needs a separately approved implementation; no model invocation is automatic.

## Focused verification

Run only the existing environment:

```sh
.venv/bin/python -m pytest -q tests/test_code_evaluator.py tests/test_evaluation_collector.py tests/test_result_evidence.py
```

Tests use synthetic fixtures, injected S3 responses, Moto/Stubber, and blocked sockets. They cover native PASS/FAIL, mapping registration before Evaluate, cached retries, forged external references, absent authentication/mapping, evaluator/job/version/authority isolation, invalid spans/targets, bounded/incomplete immutable reads, false native results without cache, and missing-judge release denial. Most focused collector fixtures isolate authority. The added originalauthoritycases integration explicitly restores REAL runs.current and exercises real current grants, admission, artifact finalization and immutable approval equality; the dedicated revocation test also verifies denial before cached return. Deterministic concurrent FoundationJobs.step interleaving verifies duplicate ownership cannot sabotage the active collector. The separate protected run collection_grant pins cases/input/binding to digest(row.approved), without amending finalized approval. These tests are not live acceptance evidence.
