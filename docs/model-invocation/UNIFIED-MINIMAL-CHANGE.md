# Opus first activation — unified minimal change request / NOT EXECUTABLE YET

Status: preparation only. No new resource, permission, budget or model invocation approved. Transport/code frozen at154a811827a0c9d588e22fdbbadd8560aefd2ae9;37e155985d1527e0bfffb6b375a4a3a658b58041 binds its rebuilt ARM evidence. No repeat search for shared Runtime and no repeat transport tests.

## Resource/change scope
| Item | Minimum proposed change | Prerequisite / unresolved exact value |
|---|---|---|
| Dedicated AgentCore Runtime | Create ONE owned Foundation Runtime, not reuse platform/shared Runtime; code PYTHON_3_13/main.py | Exact generated name after final manifest; returned runtime ID/version cannot be invented |
| Foundation execution role | Reuse existing dedicated Foundation role, not create another IAM role; retain direct-provider Deny | Exact role captured privately; trust/runtime-name compatibility must survive final binding |
| Model Gateway/outbound role | Reuse Gateway/target; append only Opus profile+readback member resources to existing three IAM statements, preserve Haiku/other Deny | Fresh before/apply drift check; prior exact private diff exists but not approved |
| Cedar/schema | Add Opus-only stable-principal permit; nonstream,thinking disabled,max_tokens<=256; reviewed system/thinking schema | Service validation not performed; updated targetDigest requires explicit binding review |
| Immutable artifact | Upload versioned final artifact into existing approved bucket/prefix; no new bucket proposed | Final hash/S3 versionId NOT AVAILABLE; current base is synthetic/unadmitted |
| Network | Bind existing approved VPC subnets and security groups | Exact approved subnet/SG/egress configuration NOT AVAILABLE. No new VPC,NAT or private endpoint implicitly requested; if required, separate priced delta |
| Producer/admission | Bind finalized source,role,network,artifact,definition; enable reviewed Worker producer/live path only afterwards | Concrete production diagnostic admission adapter and trusted user/agent identity still absent; no switch-only activation |
| Evidence/budget | Reuse reviewed persistent claim+aggregate ledger; restricted diagnostic response receipt | Actual all-service USD reservation,pricing basis and retention limits NOT APPROVED |

Existing exchange AWS_IAM route is a product admission component,not automatic authorization for the independent capture sender. Its existence does not prove a deployed caller. New Runtime also does not automatically create a safe capture entrypoint.

## Artifact and identity dependency sequence
1. Freeze exact reviewed capture runner/admission implementation plus execution source. Current standalone sender is not a deployed diagnostic endpoint.
2. Select exact dedicated role and approved existing VPC configuration; derive trusted user/agent budget scopes from authenticated authority,not payload.
3. Issue separately reviewed capture authority binding endpoint,request digest,role,project,expiry,and approved cost envelope. Commit reserve+claim before send; timeout retains all held funds and never retries.
4. Capture path must accept unknown response identity only into restricted diagnostic evidence,without modifying production Model allowlists/Ready/approval. This requires a distinct reviewed executable package; existing synthetic production base MUST NOT substitute for it.
5. After explicitly approved single capture through owned Foundation/Gateway,verify actual response identity,then pin it into production manifest. Generate admission tuple(endpoint,manifest_digest,foundation_digest,runtime_role,binding_ref); tuple digest is integrity linkage,not approval signature.
6. Build/finalize exact admitted ZIP,upload immutable version,approve deployment policy,create/bind dedicated Runtime/version. Product Studio validation consumes a separately authorized call,not automatic permission from step4. Any capture-to-product Runtime update requires explicit exact-ID update approval; if lifecycle cannot reuse one Runtime,return for approval rather than creating a second silently.

Current ARM base ZIP SHA25646926e7e2a13d2195e3fb596fd5fe13faa5d407777738a4dbedb4729a3c9bb16 is reference evidence only. It cannot provide a final admission hash while response identity/capture runner/network remain unresolved.

## Cost boundary: actual unresolved blockers, not a guessed dollar allowance
Price-review evidence currently lists US Geo Opus rates USD5.50/1M input tokens and27.50/1M output tokens,Runtime0.0895/vCPU-hour+0.00945/GB-hour,Gateway0.005/1000calls,Policy0.000025/request. These are candidate source-extracted rates,not this operation's verified complete quote. Global5/25 rates cannot substitute for US Geo. Supporting service evidence contains tier/class alternatives and incomplete Lambda items; do not sum arbitrary rows or rely on free-tier0.

A finite total requires all of: bounded/counted input; output256; Gateway+policy request counts (including denial checks); Runtime CPU/memory and session lifetime/cleanup bound; network egress/NAT/endpoints where applicable; Lambda ARM request+compute; HTTP API; Dynamo read/write/storage including transaction amplification; S3 artifact/evidence operations+retention; logs ingestion/storage and actual traces/spans/metrics; encryption requests/data transfer if used. Unit prices without finite usage/retention limits do not prove an upper bound.

Concrete blockers to a defensible USD upper bound:
- Actual input count/bound and provider-specific counting mechanism absent.
- Runtime allocation/lifecycle maximum and approved network path absent.
- Final diagnostic/admission invocation fan-out and storage/telemetry retention limits absent.
- Applicable rates for all selected service variants not fully crosschecked.
- Explicit reviewer-approved dollar reserve absent. Existing code5USD ceiling is NOT authorization or funded budget.

Therefore total_upper_bound_usd=null and approved_reservation_usd=null. Unknown is not0. Pricing sources to reconcile: https://aws.amazon.com/bedrock/pricing/ ; https://aws.amazon.com/bedrock/agentcore/pricing/ ; AWS Price List regional supporting service records. No invoice or spend proof is claimed.

## Rollback and stop rules
Before approved apply: save exact current IAM/Cedar/target/config/artifact versions. Abort on drift. First stop new admissions; preserve CLAIMED/UNKNOWN holds and receipts. Restore only introduced Opus IAM/schema/permit deltas and Worker/config changes against recorded versions; preserve existing Haiku,general_requests policy,drafts,shared Runtime and unrelated permits. Remove only newly created dedicated Runtime after allowed retention/export and no active work; do not remove existing role/VPC/bucket. Retained artifact/log storage continues billing until approved expiry/deletion; include that interval in budget. Never overwrite concurrent changes using stale snapshots.

## Review decision requested
This document defines ONE dedicated Runtime and reuse candidates,not approval to create them. It is a consolidated incomplete change request with named blockers,not a complete executable activation packet. Do not approve blanket creation or infer permission from architecture agreement. Once exact network,capture runner/admission,and full priced bounded envelope exist,review executable diffs and reserve before any cloud mutation/inference.
