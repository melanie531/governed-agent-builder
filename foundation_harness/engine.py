"""Reusable bounded serial model/tool loop; no domain workflow imports."""
import time
from .admission import admit, recheck
from .budget import LimitReached
from .context import Denied
from .skills import instructions


class Engine:
    def __init__(self, config, authority, model, tools, telemetry):
        self.config, self.authority = config, authority
        self.model, self.tools, self.telemetry = model, tools, telemetry

    def run(self, run_ref, authenticated_entry, user_input, budget):
        started = time.monotonic()
        # Admission errors propagate before any service can be dispatched.
        binding = admit(self.authority, run_ref, authenticated_entry, self.config)
        budget.deadline = min(budget.deadline, time.monotonic() + max(0, binding.expires_at - time.time()))
        self.tools.reset()
        output, status = [], 'ITERATION_LIMIT'
        messages = [{'role': 'user', 'content': [{'type': 'text', 'text': user_input}]}]
        system = '\n\n'.join([p.text for p in self.config.systemPrompt] + instructions(self.config.skills))
        with self.telemetry.span('run', {'manifest_digest': binding.manifest_digest,
                                         'foundation_digest': binding.foundation_digest}) as span:
            try:
                if not isinstance(user_input, str):
                    raise ValueError('INPUT_TEXT_REQUIRED')
                for _ in range(self.config.limits.maxIterations):
                    budget.check()
                    value = self.model.generate(self.authority, binding, system, messages,
                                                self.config.tools, budget, self.telemetry)
                    messages.append({'role': 'assistant', 'content': value['content']})
                    if value['stop_reason'] != 'tool_use':
                        output = value['content']
                        status = 'SUCCEEDED' if value['stop_reason'] == 'end_turn' else 'OUTPUT_LIMIT'
                        break
                    results = []
                    for block in value['content']:
                        if block['type'] == 'tool_use':
                            content = self.tools.call(self.authority, binding, block['name'],
                                                     block['input'], budget, self.telemetry)
                            results.append({'type': 'tool_result', 'tool_use_id': block['id'], 'content': content})
                    messages.append({'role': 'user', 'content': results})
                # Revocation during the response prevents publication as success.
                recheck(self.authority, binding, 'model', self.config.model.route)
                budget.check()
            except LimitReached as exc:
                status = str(exc)
            except Denied:
                status = 'DENIED'
            except Exception:
                status = 'FAILED'
            finally:
                self.authority.finish(binding)
                self.telemetry.attributes(span, {'status': status})
        try:
            exported = self.telemetry.flush()
        except Exception:
            exported = False
        if budget.reservation:
            budget.reservation.settle({'input_tokens': budget.input_tokens,
                                       'output_tokens': budget.output_tokens}
                                      if budget.usage_known else None)
        return {'status': status,
                'execution_status': 'EXECUTION_SUCCEEDED' if status == 'SUCCEEDED' else status,
                'release_status': 'BLOCKED', 'release_code': 'EVIDENCE_INCOMPLETE', 'output': output if status == 'SUCCEEDED' else [],
                'usage': {'input_tokens': budget.input_tokens, 'output_tokens': budget.output_tokens}
                         if budget.usage_known else None,
                'model_calls': budget.model_calls, 'tool_calls': budget.tool_calls,
                'gateway_calls': budget.gateway_calls,
                'reservation_usd': str(budget.reservation_usd) if budget.reservation_usd is not None else None,
                'model_route': self.config.model.route,
                'latency_ms': round((time.monotonic() - started) * 1000, 2),
                'billing_estimate_usd': None, 'actual_cost_usd': None, 'trace_id': self.telemetry.trace_id,
                'otel_exported': exported, 'live_evidence': False,
                'production_ready': False}
