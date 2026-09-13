"""Bounded one-call Opus response-identity forensics capture.

Purpose (and ONLY purpose): perform exactly ONE no-tools, non-stream,
<=256-output-token Messages call solely to capture the real provider response
`model` identity, so the response-identity allowlist can later be populated from
REAL evidence instead of a synthetic/guessed id.

Hard invariants enforced here:
  * Exactly one call. Not a loop, no retries, no fallback. A second call raises.
  * No tools / tool_choice. Non-stream. thinking disabled. max_tokens in 1..256.
  * Explicit USD budget cap (reservation_usd, 0 < x <= 5) is mandatory.
  * The execution-role identity actually used is recorded verbatim in the receipt.
  * Capturing forensics != readiness. This entry NEVER sets/reads execution_ready,
    integration_ready or any Ready flag, and returns no admission decision.
  * Until 哥哥 applies IAM/Cedar, the live Bedrock call cannot succeed; the receipt
    is marked live_call_status='UNVERIFIED-pending-哥哥-apply' unless a real
    provider response is observed through the reviewed transport.

This module constructs no clients and creates no cloud resources. The caller
supplies an already-authorized transport (post(endpoint, body, headers, timeout))
and the execution-role identity string it is signing as.
"""
from decimal import Decimal

from .budget import Budget
from .config import Limits
from .opus_messages import REQUEST_MODEL, build_request, read_response

# One call. Full stop. This is a constant, not a configurable loop bound.
ONE_CALL = 1
MAX_OUTPUT_TOKENS = 256


class ForensicsMisuse(RuntimeError):
    """Raised when the one-call contract or fail-closed invariants are violated."""


class OpusResponseForensics:
    """Single-shot response-identity capture. Reusable object, single-use call."""

    def __init__(self, transport, endpoint, *, execution_role_arn,
                 reservation_usd, max_output_tokens=MAX_OUTPUT_TOKENS,
                 timeout_seconds=60):
        if not isinstance(execution_role_arn, str) or not execution_role_arn.strip():
            raise ForensicsMisuse('EXECUTION_ROLE_IDENTITY_REQUIRED')
        # Explicit USD budget cap is mandatory and bounded (0 < x <= 5).
        if not isinstance(reservation_usd, Decimal):
            raise ForensicsMisuse('USD_BUDGET_CAP_MUST_BE_DECIMAL')
        if not Decimal('0') < reservation_usd <= Decimal('5'):
            raise ForensicsMisuse('USD_BUDGET_CAP_OUT_OF_RANGE')
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= MAX_OUTPUT_TOKENS:
            raise ForensicsMisuse('OUTPUT_TOKEN_CAP_OUT_OF_RANGE')
        self.transport = transport
        self.endpoint = endpoint
        self.execution_role_arn = execution_role_arn
        self.reservation_usd = reservation_usd
        self.max_output_tokens = max_output_tokens
        # Hard one-call budget: exactly one model call, zero tool calls, one iteration.
        self.limits = Limits(maxIterations=ONE_CALL, maxModelCalls=ONE_CALL,
                             maxToolCalls=0, maxInputTokens=2000,
                             maxOutputTokens=max_output_tokens,
                             timeoutSeconds=timeout_seconds)
        self._spent = False

    def _receipt(self, live_call_status, response_model=None, error=None):
        # NOTE: intentionally NO execution_ready / integration_ready / ready field.
        # Capturing forensics is not a readiness signal and must not be read as one.
        return {
            'purpose': 'response-identity-forensics-capture-only',
            'request_model': REQUEST_MODEL,
            'execution_role_arn': self.execution_role_arn,
            'one_call_limit': ONE_CALL,
            'model_calls_made': ONE_CALL if self._spent else 0,
            'tool_calls': 0,
            'stream': False,
            'thinking': 'disabled',
            'max_output_tokens': self.max_output_tokens,
            'usd_budget_cap': str(self.reservation_usd),
            'captured_response_model': response_model,
            'live_call_status': live_call_status,
            'sets_ready_flag': False,
            'error': error,
        }

    def capture(self, system, prompt, headers):
        """Execute the single forensic call; return a capture-only receipt.

        The receipt records the captured response `model` id (evidence), the exact
        execution-role identity, the hard one-call limit, and the USD budget cap.
        It NEVER flips a Ready flag. If the reviewed transport cannot reach a live
        provider (e.g. IAM/Cedar not yet applied by 哥哥), the receipt is returned
        with live_call_status='UNVERIFIED-pending-哥哥-apply'.
        """
        if self._spent:
            # One call means one call. No loop, no retry, no second attempt.
            raise ForensicsMisuse('ONE_CALL_ALREADY_SPENT')
        self._spent = True

        budget = Budget(self.limits, reservation_usd=self.reservation_usd)
        body = build_request(REQUEST_MODEL, system, prompt, self.max_output_tokens)
        # Reserve/charge the single call up front so a failure still consumes it
        # (unknown outcome retains the reservation; no silent retry path exists).
        budget.model(body)

        try:
            value, _metadata = self.transport.post(
                self.endpoint, body,
                {'Accept': 'application/json', 'anthropic-version': '2023-06-01',
                 **(headers or {})},
                budget.remaining())
        except Exception as exc:  # transport/auth failure => still one call spent
            return self._receipt('UNVERIFIED-pending-哥哥-apply', error=repr(exc))

        if not isinstance(value, dict):
            return self._receipt('UNVERIFIED-pending-哥哥-apply',
                                 error='NON_DICT_PROVIDER_RESPONSE')

        response_model = value.get('model')
        if not isinstance(response_model, str) or not response_model:
            return self._receipt('UNVERIFIED-pending-哥哥-apply',
                                 error='NO_RESPONSE_MODEL_IDENTITY')

        # Validate the response shape via the same fail-closed codec, but the codec
        # requires a non-empty allowlist. During forensics the allowlist is unknown
        # by definition, so we pin it to exactly the just-observed id for structural
        # validation only. This does NOT admit or register anything; the observed id
        # is returned as raw evidence for 哥哥 to review before pinning it anywhere.
        try:
            read_response(value, (response_model,), self.max_output_tokens)
        except ValueError as exc:
            return self._receipt('OBSERVED-response-model-but-shape-invalid',
                                 response_model=response_model, error=repr(exc))

        return self._receipt('OBSERVED-live-response-model',
                             response_model=response_model)
