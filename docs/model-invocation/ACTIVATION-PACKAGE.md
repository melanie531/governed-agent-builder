# Opus activation package — review only, NO activation authorization assumed

## Fixed combination and actual artifact
Backend60aa14ee93fca2f02a771e81e2f4b635b30b22b2 + adapter9540be4706f93dadf100023d1e08658187be71b3 merged without conflicts. Source tree28eda8e5c62c7ff8dd72c8184584802ba692c41d. Excludes rejected602017de.

Built using scripts/package_foundation.py dependency_command with locked hashes, Python3.13/aarch64-manylinux2014 wheels, source_digest and package(mode=base). Actual local artifact /home/ec2-user/work/agent-studio-opus-activation-artifacts/foundation-linux-arm64-base.zip; SHA2564e79c12b47bc3cf1f62d298e75f37a9120139d01303f7dbd2d611013e4b99240,27512646bytes. ZIP CRC passes,8 native libraries all ELF AArch64(machine183),codec included. This is NOT an admitted live package: build-only synthetic response allowlist; cannot deploy it as production. Linux ARM64 execution NOT_RUN: host x86_64 and no registered ARM binfmt. Do not promote architecture inspection to execution success. After reviewer chooses an already-authorized ARM runtime/build environment, run imports+codec+signed-transport negative controls there before admission. No cloud compute created.

## Identity readback
Current Foundation IAM role exists with3 inline policies,no attached policies/boundary; current Gateway outbound role has1 inline policy,no attached policies/boundary. Foundation exact InvokeGateway Allow matches existing Gateway ARN. Cedar uses stable STS assumed-role entity without session suffix, matches Foundation role name. Gateway outbound role readback matches; Gateway/target READY,ENFORCE,no interceptor. These facts are not proof of deployed runtime using that role, user/project isolation, SCP/RCP/session-policy permissions or execution. Exact role/trust/policies/target snapshots retained privately, no identity details in public review.

## Proposed cloud delta (not applied)
Use opus-first-iam.diff for exactly Opus profile plus3 returned member resources in the three existing outbound-role statements (Deny exception,nonInvoke Deny,Invoke Allow). Preserve Haiku/other Deny/trust. Use ONLY corrected opus-256-candidate.cedar.txt and opus-256-schema.diff from a9159da lineage: Foundation stable principal,Gateway resource,target Messages action,exact us.anthropic.claude-opus-5,stream=false,max_tokens1..256,thinking.type disabled,no tools/tool_choice/output_config. system:string maxLength2048. No16-token candidate and no test-permit cleanup. Copy exact private proposals only after fresh drift comparison; redacted files cannot be applied.

## Bounded response identity capture proposal
Separate temporary diagnostic authority from business activation. After explicit review approval: use the existing Foundation workload identity via owned Runtime, not an admin substitute; one harmless predetermined text request, no tools, no retries/fallback,nonstream,256 total output cap,thinking disabled,timeout<=60s. A capture-only diagnostic records raw provider model/text/usage/HTTP request correlation in restricted evidence and must NOT set execution_ready or bypass admission. Because production response allowlist is currently unknown, do NOT weaken ModelClient allowlist to wildcard: diagnostic entrypoint/one-shot approval must be reviewed separately. Validate captured identity against requested profile/member evidence, then pin exact response allowlist into the new immutable manifest. One approved diagnostic is not permission for a second product call; allocate separate explicit one-call product verification budget after pinning. Negative unauthorized tests must stop before inference; confirm dispatch count0 rather than assume403 guarantees it.

## Budget and live acceptance gates
Existing Budget requires durable reservation for live transport and maxModelCalls1; manifest maxIterations1,maxToolCalls0,maxOutputTokens<=256. Input byte reservation+256 overhead is not exact tokenization. Dollar reservation must come from actual approved pricing/config; amount NOT YET SET, cost unknown. Receipt must track one-shot spend and unknown outcomes (no retry). Activation is blocked until pricing/reservation and executor identity/admission are fixed, not merely execution flags flipped.

Product verification: designated QA identity/project authorization -> Studio request -> owned Foundation -> exact Gateway target -> real Opus text and measured usage. Different unauthorized identity/project and wrong model denied with zero provider dispatch. Only then evaluate readiness using authoritative execution evidence; standalone Gateway200 cannot confer product readiness.

## Apply order / rollback
1 Freshly snapshot actual target,all role policies,Cedar list and current Business/Worker/Runtime package/config; invalidate candidate on drift.
2 Approve exact changes and diagnostic budget; validate Cedar against updated schema before attaching. Retain all existing policies.
3 Apply reviewed target schema addition and Opus-only IAM/Cedar; read back all; target schema changes targetDigest,so re-approve exact Haiku/Opus bindings without changing original IDs.
4 ARM execution proof, immutable source/admission bindings,actual Runtime identity and registered source/driver config; do not deploy synthetic base manifest.
5 One-shot diagnostic capture then separately authorized Studio positive/negative acceptance.
Rollback only changes introduced here: restore exact targetConfiguration,prior outbound inline policy; remove only newly created Opus policy; restore prior code/config/manifests using saved versions. Do not delete shared Gateway/Runtime/data or touch general_requests IAM. If runtime state changed after snapshot stop rollback for review rather than overwrite concurrent changes.

## Current status
Cloud writes0,inference calls0. ARM64 binary built; ARM64 execution,production response identity,actual runtime identity,pricing/reservation,Cedar service validation and live authorization tests remain NOT_RUN/unset. No activation or readiness claim.
