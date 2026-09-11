"""Production entry fails closed until native authenticated redemption is wired.

No payload or environment switch can install the controlled test authority.
Configuration is baked into the per-agent package, never overridden at invoke.
"""
from foundation_harness.context import MissingAuthority, Denied, ResolvedEntry
from foundation_harness.budget import Budget


class RuntimeHandler:
    """Integration seam for supported server-authenticated entry resolution.

    The resolver returns canonical Binding + stored input from the backend; it
    must not deserialize caller identity from payload. Production uses the dedicated IAM BackendExchange below. The supplied Engine
    executes the same generic loop.
    """
    def __init__(self, engine, resolve_authenticated_entry):
        self.engine, self.resolve = engine, resolve_authenticated_entry

    def __call__(self, payload, context):
        if not isinstance(payload, dict) or set(payload) != {'run_ref'}:
            raise Denied('ONLY_OPAQUE_RUN_REFERENCE_ALLOWED')
        entry = self.resolve(payload['run_ref'], context)
        if isinstance(entry, ResolvedEntry):
            if (entry.limits != self.engine.config.limits
                    or entry.binding.run_ref != payload['run_ref']
                    or entry.reservation.handle != payload['run_ref']):
                raise Denied('SERVER_RESERVATION_BINDING_REQUIRED')
            budget = Budget(entry.limits, reservation_usd=entry.reservation.amount_usd,
                            reservation=entry.reservation)
            return self.engine.run(payload['run_ref'], entry.binding, entry.stored_input, budget)
        # Compatibility for offline adapters only. Real transports still reject
        # this path through require_reservation before any network dispatch.
        binding, stored_input = entry
        return self.engine.run(payload['run_ref'], binding, stored_input, Budget(self.engine.config.limits))


def invoke(payload, context=None):
    if not isinstance(payload, dict) or set(payload) != {'run_ref'}:
        return {'status': 'DENIED', 'production_ready': False}
    from pathlib import Path
    import json
    import boto3
    from foundation_harness.config import load_config, digest
    from foundation_harness.backend_exchange import BackendExchange
    from foundation_harness.engine import Engine
    from foundation_harness.model_client import ModelClient
    from foundation_harness.tool_client import ToolClient
    from foundation_harness.telemetry import Telemetry, CloudWatchExporter
    from foundation_harness.transport import IAMTransport
    root = Path(__file__).parent
    admission = root / 'admission.json'
    if not admission.exists():
        return {'status': 'BLOCKED', 'code': 'AUTHENTICATED_BACKEND_REDEMPTION_NOT_CONNECTED',
                'production_ready': False}
    try:
        settings = json.loads(admission.read_bytes())
        raw = json.loads((root / 'harness.json').read_bytes())
        from foundation_harness.package_admission import validate_admission
        validate_admission(settings, raw)
        cfg = load_config(raw, settings['manifest_digest'])
        session = boto3.Session(region_name='us-west-2')
        authority = BackendExchange(session, settings['endpoint'], digest(raw))
        entry = authority.resolve(payload['run_ref'], context)
        if (entry.binding.workload != settings['runtime_role']
                or entry.binding.foundation_digest != settings['foundation_digest']):
            raise Denied('PACKAGE_WORKLOAD_BINDING_DENIED')
        budget = Budget(entry.limits, reservation_usd=entry.reservation.amount_usd, reservation=authority)
        if entry.limits != cfg.limits:
            raise Denied('SERVER_LIMITS_BINDING_DENIED')
        telemetry = Telemetry(CloudWatchExporter(session, budget=budget))
        transport = IAMTransport(session)
        engine = Engine(cfg, authority, ModelClient(transport, cfg.model),
                        ToolClient(transport, cfg.tools), telemetry)
        result = engine.run(payload['run_ref'], entry.binding, entry.stored_input, budget)
        return {**result, 'run_ref': entry.binding.run_ref,
                'manifest_digest': entry.binding.manifest_digest,
                'runtime_version': entry.binding.runtime_version}
    except Exception:
        return {'status': 'BLOCKED', 'code': 'AUTHENTICATED_BACKEND_ADMISSION_FAILED',
                'production_ready': False}


def create_app():
    from bedrock_agentcore import BedrockAgentCoreApp
    app = BedrockAgentCoreApp()
    app.entrypoint(invoke)
    return app


if __name__ == '__main__':
    create_app().run()
