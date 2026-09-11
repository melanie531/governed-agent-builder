"""Serial call limits. Unknown outcomes consume calls and retain reservations."""
from decimal import Decimal
import threading
import time

from .config import canonical


class LimitReached(RuntimeError):
    pass


class Budget:
    def __init__(self, limits, *, reservation_usd=None, reservation=None):
        self.reservation = reservation
        self.limits = limits
        self.deadline = time.monotonic() + limits.timeoutSeconds
        self.cancelled = threading.Event()
        self.model_calls = self.tool_calls = self.gateway_calls = 0
        self.input_tokens = self.output_tokens = 0
        self.usage_known = True
        self.reservation_usd = reservation_usd
        if reservation_usd is not None and not Decimal('0') < reservation_usd <= Decimal('5'):
            raise ValueError('INVALID_RESERVATION')

    def check(self):
        if self.cancelled.is_set():
            raise LimitReached('CANCELLED')
        if time.monotonic() >= self.deadline:
            raise LimitReached('TIMED_OUT')

    def remaining(self):
        self.check()
        return max(0.001, self.deadline - time.monotonic())

    def require_reservation(self, transport):
        if getattr(transport, 'requires_reservation', False):
            if self.reservation_usd is None:
                raise LimitReached('LIVE_RESERVATION_REQUIRED')
            if self.limits.maxModelCalls != 1:
                raise LimitReached('M0_ONE_INFERENCE_ATTEMPT_REQUIRED')

    def model(self, body):
        self.check()
        # Conservative byte reservation plus fixed protocol overhead, not a
        # tokenizer claim. No token-count API or hidden inference is performed.
        upper = len(canonical(body)) + 256
        if (self.model_calls >= self.limits.maxModelCalls
                or upper + self.input_tokens > self.limits.maxInputTokens
                or body['max_tokens'] + self.output_tokens > self.limits.maxOutputTokens):
            raise LimitReached('BUDGET_EXHAUSTED')
        if self.reservation:
            self.reservation.claim('model', f'model-{self.model_calls + 1}')
        self.model_calls += 1
        self.usage_known = False

    def usage(self, usage):
        if (not isinstance(usage, dict)
                or any(type(usage.get(k)) is not int or usage[k] < 0
                       for k in ('input_tokens', 'output_tokens'))):
            raise ValueError('PROVIDER_USAGE_MISSING')
        self.input_tokens += usage['input_tokens']
        self.output_tokens += usage['output_tokens']
        self.usage_known = True
        if (self.input_tokens > self.limits.maxInputTokens
                or self.output_tokens > self.limits.maxOutputTokens):
            raise LimitReached('PROVIDER_USAGE_EXCEEDS_RESERVATION')

    def tool(self):
        self.check()
        if self.tool_calls >= self.limits.maxToolCalls:
            raise LimitReached('BUDGET_EXHAUSTED')
        if self.reservation:
            self.reservation.claim('tool', f'tool-{self.tool_calls + 1}')
        self.tool_calls += 1

    def gateway(self):
        self.check()
        if self.gateway_calls >= 8:
            raise LimitReached('GATEWAY_CALL_CAP')
        if self.reservation:
            self.reservation.claim('gateway', f'gateway-{self.gateway_calls + 1}')
        self.gateway_calls += 1
