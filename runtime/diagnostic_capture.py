"""Separate diagnostic package entry; never registered by the product Runtime.

The immutable capture-settings.json file is intentionally NOT shipped. Operator
approval must bind its exact table/region and the final diagnostic artifact.
"""
import json
from pathlib import Path


def invoke(payload, context=None):
    # No env enable, run_ref compatibility, payload identity or context headers.
    settings_path = Path(__file__).with_name('capture-settings.json')
    if not settings_path.is_file():
        return {'outcome': 'DENIED', 'code': 'DIAGNOSTIC_NOT_CONFIGURED'}
    try:
        if not isinstance(payload, dict) or set(payload) != {'capture_ref'}:
            raise ValueError('ONLY_DIAGNOSTIC_REFERENCE_ALLOWED')
        settings = json.loads(settings_path.read_bytes())
        if (set(settings) != {'region', 'state_table'} or settings['region'] != 'us-west-2'
                or not isinstance(settings['state_table'], str) or not settings['state_table']):
            raise ValueError('IMMUTABLE_DIAGNOSTIC_SETTINGS_REQUIRED')
        import boto3
        from botocore.config import Config
        from backend.diagnostic_capture import capture
        from backend.dynamo_store import DynamoStore
        from foundation_harness.transport import IAMTransport
        session = boto3.Session(region_name=settings['region'])
        # Even metadata calls have no automatic retries; price their bounded fanout.
        config = Config(retries={'total_max_attempts': 1}, connect_timeout=3, read_timeout=5)
        store = DynamoStore(settings['state_table'], resource=session.resource('dynamodb', config=config))
        return capture(store, payload, sts=session.client('sts', config=config),
            control=session.client('bedrock-agentcore-control', config=config),
            transport=IAMTransport(session))
    except Exception:
        # No raw exception/provider content, and no retry even after UNKNOWN.
        return {'outcome': 'DENIED', 'code': 'DIAGNOSTIC_CAPTURE_DENIED'}


def create_app():
    from bedrock_agentcore import BedrockAgentCoreApp
    app = BedrockAgentCoreApp()
    app.entrypoint(invoke)
    return app


if __name__ == '__main__':
    create_app().run()
