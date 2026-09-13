# Parent-checked pricing evidence and corrections — no activation

Scope remains Studio -> owned Foundation Runtime -> Gateway -> Bedrock Runtime. Worker pricing report arrived after unified change request cf56f855. It does not remove deployment/admission/budget blockers.

## Source-backed rates, not a full approved quote
Raw worker crosscheck JSON inspected by parent: US Geo/In-region Opus5 input USD5.50/1M tokens, output USD27.50/1M tokens. Corresponding AWS Price List rate codes VPSX8DAZBRJ5FXAC.4799GE89SK.6YS6EN2CT7 and UV37DGD9852Q2CCN.4799GE89SK.6YS6EN2CT7, effective2026-08-01; AWS pricing feed publication2026-09-11. Global rates5/25 do not apply to this us. profile.

Calculated using Decimal:256output tokens at27.50/M = USD0.00704 (model output only).2000input tokens at5.50/M = USD0.011 ONLY IF that input-token ceiling is demonstrably enforced; a manifest field alone is not such evidence. These are neither full-service totals nor approved reserves.

Official AgentCore price table: Runtime CPU USD0.0895/vCPU-hour,memory USD0.00945/GB-hour; Gateway USD0.005/1000invocations; Policy USD0.000025/authorization request. Policy page source exists but worker did not locate a matching Price List SKU. No free-tier assumptions.

Sources: https://aws.amazon.com/bedrock/pricing/ ; https://aws.amazon.com/bedrock/agentcore/pricing/ ; https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/bedrockfoundationmodels/USD/current/bedrockfoundationmodels.json ; AWS Price List API,regionCode us-west-2.

## Corrections to worker summary
1. The statement that the diagnostic bypasses AgentCore Runtime is NOT applicable. Required chain includes owned Foundation Runtime; Runtime cost must remain in the envelope.
2. "Idle is free" cannot erase memory charges. Parent read raw official pricing page: no CPU use during I/O wait means no CPU charge, while memory is charged for peak memory consumed up to that second; session includes boot,initialization,idle through termination and system overhead,128MB memory minimum. Runtime lifetime is still a cost-bound variable.
3. Actual input usage is unknown and maxInputTokens2000 must not be relabeled a verified provider token bound.
4. Supporting-service JSON available at review did not corroborate normal Lambda ARM duration with the claimed SKU; request-only or provisioned-concurrency rates are not substitutes. Treat the claimed compute rate as unverified until exact applicable entry is supplied.
5. Missing Policy SKU is a source-coverage gap,not evidence the policy is free. Control-plane permission preparation did not execute inference; no zero-cost guarantee asserted for the eventual workflow.

## Still needed for a defensible all-service maximum
Final network path (existing VPC/egress or separately approved priced resources); Runtime session/lifecycle bound; measured/bounded input; exact diagnostic+admission call counts; applicable Lambda duration rate; Dynamo transaction/read amplification; versioned S3/log retention; Transaction Search span ingestion/indexing classification; all used KMS/network/telemetry costs. Zero indexed tools does not authorize assuming unknown INFERENCE indexing charge is0.

TOTAL_UPPER_BOUND_USD=null. APPROVED_RESERVATION_USD=null. Production caller/admission and runtime creation remain unapproved. Price-only evidence and machine-readable tokens/AgentCore price tables accompany this note; no resource identifiers/inventory or private role snapshots published. No code changes,no cloud writes,no model invocation.
