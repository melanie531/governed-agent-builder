# Cost boundary candidate: failed preflight, NOT ready for activation

Candidate JSON and offline checker accompany this note. No product/codec/transport change and no cloud writes. Candidate is not an executable AWS payload: null identities/empty network and unresolved enforcement are deliberate blockers,not defaults.

Actual command: python scripts/check_cost_boundary_candidate.py -> exit2,BLOCKED,11 unresolved fields. It validates parameter presence only,not effective authorization,price truth or deployment safety. Even exit0 would require separate review; setting verification strings is not evidence.

## Proposed mechanisms versus actual enforcement
- Runtime lifecycleConfiguration: idleRuntimeSessionTimeout60,maxLifetime60. Official runtime-lifecycle-settings.md specifies minimum60 seconds and termination can take15 seconds. This is per-microVM,not total invocation/session count; session may continue in a new instance. Existing deployment adapter does NOT yet send these fields. Candidate NOT applied. Verification must inspect actual create payload/readback and session termination; cannot price total Runtime as60s merely from this JSON.
- Input4096UTF8 bytes: proposed before-dispatch guard only,NOT implemented by this JSON. Bytes are not provider tokens. Current Budget.model uses serialized-byte reservation+256 overhead; that is not proven Opus token count. Provider token upper bound remains unknown and excluded from any asserted total.
- S3 evidence prefix: candidate current/noncurrent expiry1day,multipart abort1day and expired-marker cleanup. Exact bucket/prefix required; merge with existing lifecycle rules rather than replace the whole bucket policy. Deletion is asynchronous,not a physical deletion deadline. Verification: put/get lifecycle readback only after approval,actual matching object/version retention and billing semantics per official docs. Candidate NOT applied; no storage duration/volume hard bound yet.
- Logs retention1day: proposed dedicated log group only,not shared groups. Ingestion byte cap absent; retention alone cannot bound log costs. No PutRetentionPolicy issued.
- Role/network: existing dedicated role readback is available privately,not newly discovered absent. Public candidate does not embed ARN. Exact approved role,existing subnet/SG/egress must be supplied to a private reviewed payload. No shared Runtime reuse,new VPC/NAT/endpoint creation or PUBLIC fallback.

Remaining parameters in actual checker output: runtime.network.subnets,securityGroups,roleArn,session_count_enforcement; input.provider_token_upper_bound;storage.bucket,evidence_prefix;logs.logGroupName,ingestion_byte_cap;budget.approved_reservation_usd,full_service_upper_bound_usd.

Sources fetched this turn: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-lifecycle-settings.md ; https://docs.aws.amazon.com/AmazonS3/latest/userguide/lifecycle-expire-general-considerations.html ; https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_PutRetentionPolicy.html . Source fetches succeeded; no service configuration calls made. Known unit prices remain in PRICE-REVIEW-CORRECTIONS.md,not transformed into an unsupported complete quote.

This delivery is concrete failed preparation evidence and candidate configuration,not a request to approve broad resource creation. Identity/admission implementation remains peer-owned. No Runtime created,no permissions changed,no inference run.
