# Self-service admission: regression correction

## What was wrong

The previous implementation required a human platform administrator to approve each exact business agent/version/prompt/evaluation definition, with a one-hour expiry. The normal live Deploy path consumed that human-reviewed record. This was a product regression, not an approved product requirement, and not an already-isolated M0 design. Renaming the old behavior would not fix it.

The product requirement is business self-service configuration and one-click deploy/test within a previously reviewed Foundation. This patch corrects the backend admission caller chain. It does **not** claim completed cloud or UI integration.

## Two explicit paths

### Product path

1. Platform admin registers a reviewed versioned executable source through the existing Foundation source API. Endpoint, workload role, configuration, component catalog hashes and Foundation manifest remain server-owned.
2. Platform admin approves `/api/admin/foundation-policies` once per policy revision. The approval binds that exact source record, Foundation manifest, allowed capabilities, workspaces, data sources, incompatible capability pairs, prompt and dataset bounds. Runtime limits and mandatory runtime policy are inherited read-only from the source. Approval updates the governance epoch and retains an immutable historical revision.
3. An authenticated business member creates/revises their own domain definition using the existing API. A prompt or dataset revision is a new immutable version with new hashes, not a new human approval requirement.
4. `/api/agents/{id}/deploy-test` with `execution_mode=live` calls `FoundationJobs.enqueue -> self_service_admission.admit`. The backend checks current owner/workspace/business membership, exact version and definition digest, current grants, component compatibility and versions, Foundation/source approval, data/risk bounds, policy and grant epochs. A server-injected evaluator can add risk checks; it cannot bypass the mandatory checks.
5. The backend writes an immutable `policy-admission` receipt and separate one-hour request authority in the same repository transaction/CAS as the job. Clients cannot supply approval/ready/admin, identity, receipt, endpoint, role or limits. `/api/agents/{id}/admission` also exposes this backend operation independently and returns `ADMITTED_NOT_RELEASED`, never READY.
6. Missing final artifact produces a persisted `WAIT_ARTIFACT` job, not an admin review task or fixture fallback. The async worker consumes the mechanical producer's exact artifact binding when available. It then applies the existing Linux entry proof, artifact readback, runtime admission JSON, exact manifest/version, dedicated workload role, all-service budget, Runtime READY, persistent call authority, trace readback and evaluation release gates.

The expiry of request authority forces automatic current-policy revalidation by the authenticated owner. It does not force a new admin review. The immutable receipt and finalized artifact digest are not rewritten to renew a timestamp. Runtime/worker calls never silently renew their own authority; session, membership, execution and job deadlines remain independently enforced.

A changed grant/policy epoch, Foundation/source/catalog revision, or current eligibility invalidates the old receipt. Reusing it fails; a fresh domain version and retest are required. Revocation is checked again at execution, including current grant queries rather than only cached epochs. No previous successful evidence transfers to a new version.

### Exceptions and M0

- A new unapproved capability uses the existing `/api/requests` workflow. Grant approval never creates a connector or bypasses Foundation compatibility.
- Foundation/data/risk exceptions can be submitted through `/api/agents/{id}/exceptions`; only a platform admin can decide `/api/admin/domain-exceptions/{request_id}/decision`. Requests are bounded and expire after one hour. Approval requires the requested source/capabilities/data policy and grants to have been explicitly approved first. It cannot override the structural security checks. An exact matching decision yields distinct `human-exception` provenance; ordinary permitted revisions do not call the human review function.
- Exact-definition manual review remains only at `/api/internal/m0/foundation-approvals`, hosted-admin authenticated and disabled unless the internal `FOUNDATION_M0_PROBE_ENABLED=1` switch is explicitly configured. The old `/api/admin/foundation-approvals` route is removed. Business Deploy never accepts an M0/legacy receipt, even with that internal switch enabled.
- New manual receipts explicitly record `M0`. Old unlabelled/manual records are **not** reinterpreted as product authorization. Migration requires approving the Foundation policy and creating a new business domain version; history/artifacts are preserved, not overwritten.

## Receipt and transaction binding

Each receipt records server-authenticated creator/workspace, agent/version/definition hash, actual prompt/dataset/rubric hashes, pinned component versions and catalog digest, Foundation approval revision and policy digest, source revision/digest, current grant epoch, global policy version/digest and eligibility. The immutable receipt is stored independently by server-generated ID. Current request authority binds its digest. SQLite uses `BEGIN IMMEDIATE`; DynamoDB uses the existing global revision CAS, including reads during verification. A concurrent revocation prevents admission/finalization commit.

`runtime_admission_reviewed` remains the legacy package contract field. For product receipts it means the immutable runtime configuration inherited from reviewed Foundation policy has passed backend admission, **not** that a person reviewed the domain prompt. Receipt provenance makes the difference explicit. No code writes DynamoDB `ready=true`.

## Remaining integration gaps, deliberately fail-closed

- `scripts/verify_package_admission.py:read_platform_approval` still requires an admin-shaped per-definition receipt and `foundation-review` readback. Those producer scripts were outside this task's ownership. They must be updated to consume protected policy-admission receipts/current authority without fabricating admin provenance. Until that is done, the actual live package producer cannot complete the new product path.
- The existing finalization HTTP API remains hosted-admin-only. Its backend verifier now recognizes policy receipts while retaining all static artifact/readback/Linux checks, but workload-authenticated mechanical finalization is not yet wired. Routine UI must not expose an Admin Finalize click as a workaround.
- `WAIT_ARTIFACT` is a bounded async consumer, not a newly implemented package/build service. Missing producer delivery remains waiting until deadline, then BLOCKED. The cloud SQS wiring and producer IAM must be reviewed separately. No new authority, crypto system or gateway was introduced.
- The existing live executor still enforces one model call and one dataset case, dedicated-role constraints, and its reviewed budget envelope. Admission can authorize a larger policy-bounded dataset, but that does not claim the executor can run the full dataset today. This patch does not delete those execution limits to manufacture success.
- The existing live catalog/execution integration switch still fails closed when not configured. No UI was changed; the current UI's fixture/live selection and production one-click wiring remain unverified.
- Data-source labels are schema-restricted, not semantic DLP proof. Restricted data is not enabled by this patch. A semantic risk evaluator/approved data connector must be wired before claiming support beyond the existing bounded synthetic-source contract.
- Existing deployment/source discovery performs cloud checks only when operators use those APIs. This implementation task made no cloud calls or human-identity tests. Offline tests use synthetic principals, SDK doubles and Moto. Runtime import smoke and any separate worker's cloud results are unrelated and are not inherited as evidence.

## Security and architecture review

| Pillar | Status |
|---|---|
| Security | Current server identity/grants, readonly template policy, distinct provenance, exact hashes, CAS, expiry and artifact guards retained. Producer workload identity wiring remains incomplete. |
| Reliability | Persistent WAIT_ARTIFACT continuation, deadline, replay/version fencing; missing artifacts fail closed. No READY shortcut. |
| Performance | Admission is local policy evaluation; no new per-revision human/cloud round trip. Producer latency remains unverified. |
| Cost | Existing all-service reservation and persistent call caps retained; request/queue limits remain. No cloud resources created. |
| Operational excellence | Immutable receipt/policy history, exception audit and explicit blocked integration states. |
| Sustainability | No new infrastructure or redundant Runtime execution; no cloud efficiency claim. |

## Verification scope

Targeted tests cover initial business admission and prompt/dataset revisions without human review; cross-user denial; real capability/exception requests; revoked Foundation/grants; stale epochs/policies/versions; conflicting capabilities; payload approval/role forgery; authority expiry with automatic revalidation; CAS revocation races; M0 migration isolation; artifact/Linux guards; and downstream synthetic runtime/budget/trace/evaluation checks. The full `tests/` suite is run after the targeted suites. Local verification on 2026-09-12: targeted approval/admission/finalization/wiring suites **124 passed** (2 warnings); complete `tests/` suite **640 passed** (3 warnings). Both ran with synthetic AWS test credentials and metadata lookup disabled. Committed source SHA is reported separately; tests are not cloud deployment or end-to-end production proof.
