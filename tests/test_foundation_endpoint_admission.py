"""SDK metadata / synthetic clients only; no cloud or Runtime execution proof."""
import re
from types import SimpleNamespace
import pytest
from botocore.exceptions import ClientError
from botocore.session import Session
from botocore.validate import validate_parameters
from fastapi.testclient import TestClient
from foundation_harness.context import Denied
from backend.app import create_app
from backend.foundation_jobs import FoundationJobs
from tests.test_foundation_wiring import install
from tests.test_runtime_deployment import ARN
from tests.test_serverless import cloud, sign_in
from tests.conftest import create


@pytest.mark.parametrize('qualifier, valid_endpoint_name', [('DEFAULT', True), ('3', False)])
def test_installed_sdk_qualifier_is_endpoint_name(qualifier, valid_endpoint_name):
    session = Session()
    invoke = session.get_service_model('bedrock-agentcore').operation_model('InvokeAgentRuntime').input_shape
    lookup = session.get_service_model('bedrock-agentcore-control').operation_model('GetAgentRuntimeEndpoint').input_shape
    validate_parameters({'agentRuntimeArn': ARN, 'qualifier': qualifier,
                         'runtimeSessionId': 's' * 33, 'payload': b'{}'}, invoke)
    # Invoke's qualifier is a plain string in this SDK; validation does not enforce
    # endpoint-name regexes. Check the installed control-plane shape explicitly.
    pattern = lookup.members['endpointName'].metadata['pattern']
    assert (re.fullmatch(pattern, qualifier) is not None) is valid_endpoint_name


@pytest.mark.parametrize('change', [
    {'liveVersion': '4'}, {'targetVersion': '4'}, {'status': 'UPDATING'},
    {'name': '3'}, {'agentRuntimeArn': 'other-runtime'}, {'targetVersion': None},
])
def test_endpoint_exact_version_guard(change):
    response = {'name': 'DEFAULT', 'status': 'READY', 'agentRuntimeArn': ARN,
                'liveVersion': '3', 'targetVersion': '3'}
    calls = []
    def read(**kw): calls.append(kw); return {**response, **change}
    jobs = FoundationJobs(SimpleNamespace(adapter=SimpleNamespace(client=SimpleNamespace(get_agent_runtime_endpoint=read))), None, None)
    with pytest.raises(Denied, match='ENDPOINT_VERSION_NOT_READY'):
        jobs.invocation_endpoint({'runtime_id': 'synthetic-abc', 'runtime_arn': ARN, 'runtime_version': '3'})
    assert calls == [{'agentRuntimeId': 'synthetic-abc', 'endpointName': 'DEFAULT'}]


@pytest.mark.parametrize('when', ['readiness', 'before-invoke'])
def test_retargeted_endpoint_blocks_actual_studio_caller(cloud, payload, tmp_path, when):
    original, client, _ = cloud
    sign_in(cloud); payload['dataset'] = payload['dataset'][:1]
    definition = create(client, payload)
    service, control, runtime = install(original.state.store, definition, tmp_path)
    app = create_app(repository=original.state.store, worker_enabled=False, foundation_jobs=service)
    app.state.hosted_auth.keys = original.state.hosted_auth.keys
    read = control.get_agent_runtime_endpoint
    with TestClient(app, base_url=str(client.base_url), cookies=client.cookies, headers=client.headers) as c:
        result = c.post(f"/api/agents/{definition['agent_id']}/deploy-test", json={
            'version': 1, 'execution_mode': 'live', 'idempotency_key': 'endpoint-regression'})
        assert result.status_code == 202, result.text
        job = result.json()['job_id']
        app.state.step_job(job)
        if when == 'before-invoke':
            app.state.step_job(job)
            assert c.get('/api/jobs/' + job).json()['stage'] == 'RUNNING'
        control.get_agent_runtime_endpoint = lambda **kw: {**read(**kw), 'liveVersion': '4', 'targetVersion': '4'}
        app.state.step_job(job)
        output = c.get('/api/jobs/' + job).json()
        assert output['stage'] == 'BLOCKED'
        assert output['result']['failure']['code'] == 'RUNTIME_ENDPOINT_VERSION_NOT_READY'
        assert runtime.calls == []
        app.state.step_job(job)
        assert runtime.calls == []


@pytest.mark.parametrize('when', ['readiness', 'before-invoke'])
def test_endpoint_lookup_failure_blocks_actual_studio_caller(cloud, payload, tmp_path, when):
    original, client, _ = cloud
    sign_in(cloud); payload['dataset'] = payload['dataset'][:1]
    definition = create(client, payload)
    service, control, runtime = install(original.state.store, definition, tmp_path)
    app = create_app(repository=original.state.store, worker_enabled=False, foundation_jobs=service)
    app.state.hosted_auth.keys = original.state.hosted_auth.keys
    lookups = []
    def unavailable(**kw):
        lookups.append(kw)
        raise ClientError({'Error': {'Code': 'ResourceNotFoundException',
                                    'Message': 'Synthetic endpoint unavailable'}},
                          'GetAgentRuntimeEndpoint')
    with TestClient(app, base_url=str(client.base_url), cookies=client.cookies, headers=client.headers) as c:
        result = c.post(f"/api/agents/{definition['agent_id']}/deploy-test", json={
            'version': 1, 'execution_mode': 'live', 'idempotency_key': 'endpoint-lookup-failure'})
        assert result.status_code == 202, result.text
        job = result.json()['job_id']
        app.state.step_job(job)
        assert c.get('/api/jobs/' + job).json()['stage'] == 'WAIT_RUNTIME'
        if when == 'before-invoke':
            app.state.step_job(job)
            assert c.get('/api/jobs/' + job).json()['stage'] == 'RUNNING'
        control.get_agent_runtime_endpoint = unavailable
        app.state.step_job(job)
        output = c.get('/api/jobs/' + job).json()
        assert output['stage'] == 'BLOCKED'
        assert output['result']['failure']['code'] == 'LIVE_STEP_FAILED'
        assert len(lookups) == 1 and lookups[0]['endpointName'] == 'DEFAULT'
        assert runtime.calls == []
        app.state.step_job(job)
        assert len(lookups) == 1
        assert runtime.calls == []
