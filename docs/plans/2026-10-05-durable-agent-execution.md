# Continue agent work without a fixed execution budget

Status: **superseded and rolled back at the user's request.** The active change
restores the original invocation path and removes only its 150-second check.
Deployment and remaining acceptance evidence:
`artifacts/studio-runtime-timeout-only-20261005/RELEASE.md`.

## Observed failure

The user's invocation returned five successful Snowflake tool results before
the foundation raised `Agent invocation exceeded its time budget`. The loop
checked a hardcoded 150 seconds between turns and allowed only six tool calls.
The worker's transport, Lambda lifetime, Runtime session lifetime, receipt
size, and queue continuation also prevent solving this by deleting one check.

## Required behavior

- No total time or tool-call budget for the agent's logical invocation.
- Save progress after each model or tool operation. Continue in another
  Runtime session and worker delivery without replaying completed operations.
- Keep the immutable agent definition, tool allowlist, argument validation,
  schema checks, per-user authorization, and current membership checks.
- Keep individual transport timeouts. An uncertain external operation must
  be reconciled from its exact receipt; never blindly execute it again.
- Keep model context and individual evidence pages bounded by compacting
  completed work and retaining the original operation receipts separately.
- Show progress and allow the owning user to stop after the current operation.
- Preserve existing deployments. Enable the new execution protocol only with
  a matching, newly published foundation artifact and newly deployed version.
- The user's own agent, MCP configuration, queries, and credentials are not
  changed or invoked by this implementation task.

## Design

1. Add a checkpointed foundation execution protocol. A step discovers selected
   tools, calls the model, calls one requested tool, or compacts completed
   context. A successful step has an immutable S3 receipt and a private
   checkpoint. No token or OAuth challenge is checkpointed.
2. Pin checkpoints to the logical request, definition, input, prior receipt,
   and step number. Give each step its own physical Runtime session. Keep the
   logical trace identity across steps.
3. Store only receipt references and bounded progress in the job ledger.
   Keep full operation evidence in versioned, encrypted private S3 objects.
   Checkpoint reads must match the exact definition/request prefix.
4. Continue through transactional outbox events delivered by the existing
   DynamoDB stream dispatcher. Do not create a recursive Worker-to-SQS chain.
5. Recheck authorization and refresh the server-side Studio session before
   every operation. Reconcile a lost worker from the saved step receipt. If
   completion cannot be established, report uncertainty without another call.
6. Remove the one-hour deadline for invocation/evaluation jobs using the new
   protocol. Deployment and cleanup deadlines remain separate lifecycle rules.
7. Provide an owner-scoped stop action, persist its intent, and check it at
   operation boundaries. Do not report an in-flight external call as cancelled.

## Test-first implementation

1. Reproduce the time and six-call failures with a finite scripted model and
   simulated elapsed time.
2. Test more than six calls across fresh Runtime instances, checkpoint binding,
   opaque model continuation, context compaction, structured errors, and consent.
3. Exercise real S3 conditional/versioned receipts with the existing AWS
   emulator. Verify replay rejection and recovery after a lost acknowledgement.
4. Test many worker deliveries, outbox dispatch, old-runtime compatibility,
   cancellation races, authorization revocation, and work beyond one hour.
5. Test the progress and Stop controls through the rendered portal contract.
6. Run affected backend suites, frontend build, compilation, IaC/security/tag
   checks, and `git diff --check`.

## Release and acceptance

Prepare scoped backend, foundation, frontend, and IaC artifacts, rollback
instructions, and sanitized evidence before requesting release approval.
The supplied AGENTS.md requires explicit approval before deploying over the
accepted application. Do not merge or deploy as an implied part of this fix.

After an approved release, hosted acceptance must use the authenticated portal.
The user revises and deploys their existing agent to adopt the new foundation,
then sends a fresh question. A backend-only invocation is not hosted acceptance.
Keep local verification, release status, and hosted acceptance distinct.

## Progress

- Read the exact failed invocation and correlated model/tool receipts.
- Inspected the current branch, dirty tree, harness, Runtime, worker, queue,
  authorization renewal, evidence, and portal contracts.
- Implemented immutable per-operation checkpoints, receipt reconciliation,
  transactional stream dispatch, protocol-version gating, owner-scoped Stop,
  and fresh-session continuation of saved questions.
- Removed the overall time/tool-count limits; old deployed versions remain
  pinned until the user revises and deploys them.
- Affected Python suites: 300 passed. Rendered browser checks: 2 durable
  journeys and 10 existing journeys passed. Build, compilation, package
  integrity and diff checks passed.
- Prepared bounded backend/foundation ZIPs from verified live base artifacts,
  frontend assets and native-derived IaC deltas. Read-only live preflight and
  static security/tag checks passed.
- Stored reviewable artifacts, sanitized evidence, deployment/rollback steps
  and the manual portal acceptance journey in
  `artifacts/studio-durable-execution-20261005/RELEASE.md`.
- The user approved deployment. Native template validation, stack updates,
  Lambda/foundation/frontend publication and the 90-check live security/tag
  audit passed. Authenticated portal loading was checked; full hosted query
  acceptance remains open.
- The user's new run failed before OAuth because checkpoint lookup lacked
  `s3:ListBucket`. A one-role repair is deployed and verified; 55 affected
  tests and four live IAM simulations passed.
- The user subsequently required rollback of the expanded scope. The original
  backend, portal, queue filter and IAM policy are restored. Both affected
  Runtime deployments now use the original protocol with only the 150-second
  check removed. Agent definitions and MCP/OAuth setup were preserved, and no
  question was replayed. A fresh hosted query remains unverified.
