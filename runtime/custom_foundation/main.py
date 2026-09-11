"""Production entry fails closed until native authenticated redemption is wired.

No payload or environment switch can install the controlled test authority.
Configuration is baked into the per-agent package, never overridden at invoke.
"""
from foundation_harness.context import MissingAuthority, Denied
from foundation_harness.budget import Budget


class RuntimeHandler:
    """Integration seam for supported server-authenticated entry resolution.

    The resolver returns canonical Binding + stored input from the backend; it
    must not deserialize caller identity from payload. No production resolver is
    installed in this slice. The supplied Engine executes the same generic loop.
    """
    def __init__(self, engine, resolve_authenticated_entry):
        self.engine, self.resolve = engine, resolve_authenticated_entry

    def __call__(self, payload, context):
        if not isinstance(payload, dict) or set(payload) != {'run_ref'}:
            raise Denied('ONLY_OPAQUE_RUN_REFERENCE_ALLOWED')
        binding, stored_input = self.resolve(payload['run_ref'], context)
        return self.engine.run(payload['run_ref'], binding, stored_input, Budget(self.engine.config.limits))


def invoke(payload, context=None):
    if not isinstance(payload, dict) or set(payload) != {'run_ref'}:
        return {'status': 'DENIED', 'production_ready': False}
    try:
        # SDK context is not assumed to authenticate a human or distinct Runtime.
        MissingAuthority().redeem(payload['run_ref'], context)
    except Denied:
        return {'status': 'BLOCKED', 'code': 'AUTHENTICATED_BACKEND_REDEMPTION_NOT_CONNECTED',
                'production_ready': False}


def create_app():
    from bedrock_agentcore import BedrockAgentCoreApp
    app = BedrockAgentCoreApp()
    app.entrypoint(invoke)
    return app


if __name__ == '__main__':
    create_app().run()
