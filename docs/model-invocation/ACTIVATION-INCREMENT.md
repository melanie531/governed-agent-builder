# Activation preparation increment, fixed product source preserved

Base7e595fa. No product source changes, IAM/Cedar application, inference or cloud resource creation.

## ARM64 execution evidence
Existing host binfmt now has qemu-aarch64 enabled (not installed/changed by this operation). Pulled official python:3.13-slim ARM64 image and ran the exact extracted4e79c12b...99240 base artifact in Docker --platform linux/arm64 --network none --read-only --cap-drop ALL --security-opt no-new-privileges. CPU1,memory512MiB,tmpfs32MiB. First run failed Permission denied reading probe because local umask excluded nonowner access; corrected only local nonsecret probe/extracted file modes, not bytes. Every extracted file compared byte-for-byte to archive after execution.

Actual execution output: aarch64,Python3.13.15,packaged pydantic_core native module imported,manifest roundtrip,codec ran,synthetic SigV4 passthrough returned; empty response allowlist/direct Runtime rejected. Exit0. Network disabled,real credentials absent,inference0. This is emulated Linux ARM64 execution on x86_64, NOT a native ARM EC2 or AgentCore Runtime success/admission proof. Image ID and archive hash in arm64-execution.json.

## Persistent one-shot proof
New test_activation_persistent_claim.py exercises existing server-owned foundation_runs via moto DynamoDB. Redeem and claim model-1 commit in distinct transactions; reopen DynamoStore to remove process-local state; repeated model-1 returns CALL_ALREADY_CLAIMED,new model-2 returns PERSISTENT_CALL_CAP. Simulated lost/unknown outcome leaves original claim persisted,settled=false and money held.1passed,2 existing warnings,1.39s. No production persistence implementation changed; no live IAM/identity isolation claim.

## Budget and capture blockers remain explicit
Current reserve() actually requires0<reservation_usd<=5 and checks cumulative account/agent/user held+estimated<=5; settled unknown outcomes do not refund. These are existing code ceilings, NOT a funded/approved live diagnostic allocation. Test fixture's0.01 is synthetic and must not become product amount. The requested model-specific dollar reservation and authoritative pricing basis remain UNSET; no paid diagnostic may start until supplied/approved and written through existing server-controlled admission. ModelCalls1,tools0,output256,timeout<=60,no retry are fixed but do not themselves prove dollar cap. Runtime/telemetry pricing must also be covered, not inference-only estimate.

Production Model continues rejecting empty or mismatch response identities. Base manifest uses synthetic allowlist solely to build/test. No Ready,approval or production identity was generated. Independent capture entrypoint still needs reviewed persistent ticket/budget and exact workload binding; do not patch production client to accept any response. Actual Runtime role proof,service Cedar validation and Studio live positive/unauthorized negative acceptance remain outstanding.
