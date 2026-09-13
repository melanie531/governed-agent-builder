# Opus 5 first bounded activation candidate — NOT APPLIED

Scope: cloud permission candidate for Studio → owned Foundation → existing Gateway → Bedrock Runtime. Product execution integration remains separately owned by the reviewer. No deployment, permission mutation, inference or Ready flag change performed.

## Actual reads and exact delta
Read current outbound Gateway role inline policy using its existing authorized account profile. GetInferenceProfile(us.anthropic.claude-opus-5) returns the exact profile ARN and three member foundation-model ARNs. Private before/after source docs are retained in /home/ec2-user/work/agent-studio-opus-first-review (mode 0700; files0600). Public review copies deliberately use descriptive placeholders and are NOT EXECUTABLE.

Append exactly those four returned resources in three existing statements:
1. DenyBedrockExceptScopedRuntimeException.NotResource — remove only Opus resources from blanket Bedrock Deny.
2. DenyNonInvokeExplicitOnExemptedResources.Resource — continue denying non-InvokeModel operations on newly exempted resources.
3. OwnerExceptionScopedRuntimeInvoke.Resource — permit InvokeModel only for the same four resources.
Whole-policy equality is asserted after removing additions. Existing Haiku resources, Mantle CreateInference Deny, other policy statements and actions unchanged. No Sonnet/Astra widening in this first candidate.

## Cedar and route
Propose a NEW Opus-only permit copied from current Foundation-only Haiku policy with exact model equality changed to us.anthropic.claude-opus-5. Keep principal, Gateway resource, Messages action, mandatory stream=false and max_tokens in 1..16 unchanged. Preserve Haiku policy and do not expand the temporary test-principal policy. This 16-token cap is a minimal response proof, NOT sufficient output budget for complete Chat/Evaluate; increase requires an explicit follow-up review.
Existing target already forwards /v1/messages to Runtime /anthropic/v1/messages and signs as bedrock. No target or outbound credential mutation is proposed for Opus. Studio/Foundation still needs exact request binding, protocol adapter and evidence-backed response allowlist activation. Current execution role identity must match the permitted Foundation principal at activation, not simply a build/test role.

## Validation and limitations
prepare_opus_first.py actually ran: four exact profile/member resources; three affected statements; all other policy content unchanged. Cedar principal/action/resource unchanged. These are structural comparisons, NOT IAM simulation or deployed Cedar validation. Other inline/managed policies, boundaries, session policies and SCP effective results remain to be checked before apply. This candidate does not establish prohibition of direct InvokeModel on the member ARNs by the Gateway role; stricter profile-only condition design is a separate explicit review question, not a claimed tested property.

Acceptance after review: explicit approved QA caller and project grant → Studio request → owned Foundation identity → Gateway → actual Opus response. Unauthorized caller/model/over-limit or streaming requests must remain denied; model=response identity must be captured as real evidence, not guessed from profile. Sonnet follows validated Messages path; Astra remains separate protocol candidate. No direct-provider/Mantle/Lambda-proxy workaround.
