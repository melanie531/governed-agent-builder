"""Real run issuer, SigV4 adapter and workload verifier; only AWS I/O is fake."""
from copy import deepcopy
from dataclasses import asdict
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend import journey_alpr, journey_alpr_run as runs, journey_alpr_workload as workloads
from backend.foundation_runs import get, put
from backend.journey import job_state
from foundation_harness.alpr_exchange import RunExchange, SpecialistExchange, CALLER_HEADER, CHANNEL
from foundation_harness.config import digest
from foundation_harness.context import Denied
from tests.test_journey_alpr_admission import setup, TOOL, ARGS


@pytest.fixture
def admitted(setup):
    journey, bound = setup
    backend = 'arn:aws:iam::123456789012:role/backend'
    gateway = 'arn:aws:iam::123456789012:role/gateway'
    rows = {}
    for name, arn, role in [('a', bound.runtime, bound.workload), ('b', bound.runtime + '-B', bound.workload + '-B')]:
        rows[name] = {'id': arn.rsplit('/', 1)[1], 'arn': arn, 'version': '1',
            'endpoint_arn': arn + '/runtime-endpoint/DEFAULT', 'configuration': {
                'roleArn': role, 'agentRuntimeArtifact': {'containerConfiguration': {
                    'containerUri': '123456789012.dkr.ecr.us-west-2.amazonaws.com/alpr@sha256:' + 'a' * 64}},
                'environmentVariables': {}, 'networkConfiguration': {'networkMode': 'PUBLIC'},
                'protocolConfiguration': {'serverProtocol': 'HTTP'}}}
    record = {**rows, 'backend_role': backend, 'gateway_role': gateway,
              'manifest_digest': bound.manifest_digest, 'specialist': workloads.specialist_binding(rows['b'])}
    by_id = {row['id']: row for row in rows.values()}
    by_role = {row['configuration']['roleArn'].rsplit('/', 1)[1]: row for row in rows.values()}
    def runtime(**kw):
        row = by_id[kw['agentRuntimeId']]
        return {'status': 'READY', 'agentRuntimeArn': row['arn'], 'agentRuntimeVersion': '1', **deepcopy(row['configuration'])}
    def endpoint(**kw):
        row = by_id[kw['agentRuntimeId']]
        return {'status': 'READY', 'agentRuntimeArn': row['arn'], 'liveVersion': '1', 'agentRuntimeEndpointArn': row['endpoint_arn']}
    control = SimpleNamespace(get_agent_runtime=runtime, get_agent_runtime_endpoint=endpoint,
        list_agent_runtime_versions=lambda **kw: {'agentRuntimes': [{'agentRuntimeVersion': '1'}]},
        get_resource_policy=lambda **kw: {'policy': json.dumps(workloads.invocation_policy(kw['resourceArn'],
            gateway if kw['resourceArn'].startswith(rows['b']['arn']) else backend))})
    iam = SimpleNamespace(get_role=lambda **kw: {'Role': {'Arn': by_role[kw['RoleName']]['configuration']['roleArn'],
        'AssumeRolePolicyDocument': workloads.trust_policy(by_role[kw['RoleName']]['arn'])}},
        list_attached_role_policies=lambda **kw: {'AttachedPolicies': []},
        list_role_policies=lambda **kw: {'PolicyNames': ['scope']}, get_role_policy=lambda **kw: {
            'PolicyDocument': {'Statement': [{'Effect': 'Allow', 'Action': 'execute-api:Invoke',
                                             'Resource': 'arn:aws:execute-api:us-west-2:123456789012:api/$default/POST/internal/journey/alpr'}]}})
    for row in rows.values():
        row['role_policies'] = {'scope': iam.get_role_policy()['PolicyDocument']}
    reference = digest(record)
    with journey.store.tx() as db:
        state = job_state(db, bound.run_ref)
        _, definition = journey.authority(db, state)
        key = journey.deployment_key(definition)
        deployed = get(db, key)
        deployed['binding']['alpr_deployment'] = reference
        put(db, key, deployed)
        put(db, workloads.PREFIX + reference, record)
        put(db, 'journey-alpr-listing:' + record['specialist']['deployment_digest'],
            {key: record[key] for key in ('b', 'gateway_role', 'specialist')})
        request_id = 'f' * 32
        cap = runs.issue_run(db, journey, state, deployed['binding'], ARGS['question'], request_id)
    principal = 'arn:aws:sts::123456789012:assumed-role/dedicated-A/session1'
    invocation = {'input': ARGS['question'], 'request_id': request_id, 'history': []}
    def dispatch(body, caller=principal):
        with journey.store.tx() as db:
            return runs.dispatch(db, journey, principal_arn=caller, body=body, control=control, iam=iam)
    return SimpleNamespace(journey=journey, bound=bound, record=record, ref=reference, cap=cap,
        dispatch=dispatch, principal=principal, invocation=invocation, control=control, iam=iam, deployed=deployed)


def client(admitted):
    adapter = RunExchange(SimpleNamespace(region_name='us-west-2'),
        'https://offline.execute-api.us-west-2.amazonaws.com/internal/journey/alpr', admitted.cap, admitted.invocation)
    adapter.transport.send = lambda url, raw, headers, timeout, service: (admitted.dispatch(json.loads(raw)), {})
    return adapter


def resolve(admitted, **extra):
    return admitted.dispatch({'operation': 'resolve-run', 'run_reference': admitted.cap,
        'invocation_digest': digest(admitted.invocation), **extra})


def test_runtime_a_adapter_resolves_issues_question_only_and_redeems_at_b(admitted):
    a = client(admitted)
    binding = a.resolve()
    assert binding.runtime_session == 'gab-' + admitted.invocation['request_id']
    name = 'alpr-investigation-specialists___' + TOOL
    a.authorize(binding, 'start', '')
    a.authorize(binding, 'tool', name)
    ref = a.issue(binding, name, ARGS)
    bbody = {k: admitted.record['specialist'][k] for k in ('runtime_arn', 'runtime_version', 'deployment_digest')}
    bprincipal = admitted.principal.replace('dedicated-A/', 'dedicated-A-B/')
    receipt = admitted.dispatch({'operation': 'redeem', 'reference': ref, **bbody,
        'tool': TOOL, 'arguments_digest': digest(ARGS)}, bprincipal)
    for view in receipt['scope']['data']:
        admitted.dispatch({'operation': 'view', 'reference': ref, **bbody, 'binding_digest': receipt['binding_digest'], 'view': view}, bprincipal)
    admitted.dispatch({'operation': 'finish', 'reference': ref, **bbody, 'binding_digest': receipt['binding_digest']}, bprincipal)
    a.authorize(binding, 'finish', '')
    with pytest.raises(Denied): a.authorize(binding, 'start', '')


@pytest.mark.parametrize('change', ['input', 'history', 'request_id', 'role', 'raw-arn', 'payload-identity', 'expired', 'record'])
def test_resolve_rejects_substitution(admitted, change):
    body = {'operation': 'resolve-run', 'run_reference': admitted.cap, 'invocation_digest': digest(admitted.invocation)}
    principal = admitted.principal
    if change in {'input', 'history', 'request_id'}:
        body['invocation_digest'] = digest({**admitted.invocation, change: 'substituted'})
    if change == 'role': principal = principal.replace('dedicated-A/', 'shared-A/')
    if change == 'raw-arn': principal = admitted.bound.workload
    if change == 'payload-identity': body['role'] = admitted.bound.workload
    if change in {'expired', 'record'}:
        with admitted.journey.store.tx() as db:
            if change == 'expired':
                row = get(db, runs.PREFIX + digest(admitted.cap)); row['binding']['expires_at'] = 0
                put(db, runs.PREFIX + digest(admitted.cap), row)
            else: put(db, workloads.PREFIX + admitted.ref, {**admitted.record, 'manifest_digest': 'f' * 64})
    with pytest.raises(Denied): admitted.dispatch(body, principal)


def test_resolve_once_and_same_iam_session_required(admitted):
    value = resolve(admitted)
    with pytest.raises(Denied): resolve(admitted)
    with pytest.raises(Denied): admitted.dispatch({'operation': 'authorize-run', 'run_reference': admitted.cap,
        'binding_digest': value['binding_digest'], 'action': 'start', 'resource': ''}, admitted.principal.replace('session1', 'session2'))


@pytest.mark.parametrize('change', ['revoke-grant', 'revoke-deployment', 'new-version', 'policy-drift'])
def test_current_authority_and_workload_rechecked_before_issuance(admitted, change):
    a = client(admitted); bound = a.resolve()
    with admitted.journey.store.tx() as db:
        if change == 'revoke-grant': db.delete('grants', where=[('persona', '=', 'alex'), ('component', '=', TOOL)])
        if change == 'revoke-deployment': put(db, workloads.PREFIX + 'revoked:' + admitted.ref, True)
        if change == 'new-version': db.update('agents', {'current_version': 2}, where=[('id', '=', job_state(db, admitted.bound.run_ref)['agent'])])
    if change == 'policy-drift': admitted.control.get_resource_policy = lambda **kw: {'policy': '{"Statement": []}'}
    with pytest.raises(Denied): a.issue(bound, 'alpr-investigation-specialists___' + TOOL, ARGS)


def test_listing_is_authenticated_and_cannot_mint_invocation_authority(admitted):
    body = {'operation': 'platform-list', **{k: admitted.record['specialist'][k] for k in ('runtime_arn', 'runtime_version', 'deployment_digest')}}
    with pytest.raises(Denied): admitted.dispatch(body)
    value = admitted.dispatch(body, admitted.principal.replace('dedicated-A/', 'dedicated-A-B/'))
    assert set(value) == {'tools', 'deployment_digest'} and value['tools'] == list(journey_alpr.TOOLS)
    with pytest.raises(Denied): admitted.dispatch({**body, 'arguments': ARGS})


@pytest.mark.parametrize('headers', [[], [(CALLER_HEADER.encode(), b'fake')], [(CALLER_HEADER.encode(), b'a'*43)] * 2])
def test_listing_adapter_uses_platform_auth_only_for_absent_header(admitted, headers):
    config = {**{k: admitted.record['specialist'][k] for k in ('runtime_arn', 'runtime_version', 'deployment_digest')},
              'endpoint': 'https://offline.execute-api.us-west-2.amazonaws.com/internal/journey/alpr',
              'channel': CHANNEL, 'channel_evidence_digest': 'e' * 64}
    b = SpecialistExchange(SimpleNamespace(region_name='us-west-2'), config)
    b.transport.send = lambda url, raw, *args: (admitted.dispatch(json.loads(raw),
        admitted.principal.replace('dedicated-A/', 'dedicated-A-B/')), {})
    request = Request({'type': 'http', 'headers': headers})
    if headers:
        with pytest.raises(Denied): b.list_tools(request)
    else: assert b.list_tools(request) == list(journey_alpr.TOOLS)


def test_no_shared_role_fallback_on_cloud_create():
    from backend.journey_cloud import JourneyCloud
    cloud = object.__new__(JourneyCloud); cloud.settings = {}
    cloud.control = SimpleNamespace(create_agent_runtime=lambda **kw: pytest.fail('shared role create'))
    with pytest.raises(ValueError, match='exact registered'):
        cloud.create({'tools': [{'name': TOOL}]}, 'token')


def test_full_runtime_a_gateway_mcp_b_and_connector_contract(admitted, monkeypatch):
    from backend import alpr
    from foundation_harness.journey_mcp import GatewayMCP
    from foundation_harness.journey_runtime import execute
    from runtime.mcp_specialist.alpr_live import LiveALPR, create_app
    from starlette.testclient import TestClient
    monkeypatch.setenv('ALPR_VIEW_SOURCE', 'live')
    monkeypatch.setenv('AWS_REGION', 'us-west-2')
    monkeypatch.setenv('ALPR_SNOWFLAKE_SSM_PREFIX', '/governed-agent-builder/alpr')
    monkeypatch.delenv('CALLER_SCOPE_PROFILE', raising=False)
    config = {**{k: admitted.record['specialist'][k] for k in ('runtime_arn', 'runtime_version', 'deployment_digest')},
              'endpoint': 'https://offline.execute-api.us-west-2.amazonaws.com/internal/journey/alpr',
              'channel': CHANNEL, 'channel_evidence_digest': 'e' * 64}
    b = SpecialistExchange(SimpleNamespace(region_name='us-west-2'), config)
    b.transport.send = lambda url, raw, *args: (admitted.dispatch(json.loads(raw),
        admitted.principal.replace('dedicated-A/', 'dedicated-A-B/')), {})
    queries = []
    def rows(query, case):
        queries.append((query, case))
        alpr._query_ids.set(({'query_id': query, 'case_id': case, 'view': alpr.VIEWS[query][0],
                             'snowflake_query_id': 'offline-' + query},))
        return []
    monkeypatch.setattr(alpr, 'live_rows', rows)
    with admitted.journey.store.tx() as db:
        state = job_state(db, admitted.bound.run_ref)
        manifest = get(db, 'journey-manifest:' + state['definition_digest'])
    name = manifest['tools'][0]['name']
    gateway = GatewayMCP(SimpleNamespace(region_name='us-west-2'), manifest['gateway_url'], alpr_admission=client(admitted))
    model_calls = []
    def converse(**request):
        model_calls.append(deepcopy(request))
        content = ([{'toolUse': {'toolUseId': 'call1', 'name': name, 'input': ARGS}}] if len(model_calls) == 1 else
                   [{'text': 'SYNTHETIC evidence complete; policies are DEMO ASSUMPTIONS.'}])
        return {'output': {'message': {'role': 'assistant', 'content': content}},
                'usage': {'inputTokens': 10, 'outputTokens': 10}, 'stopReason': 'end_turn'}
    with TestClient(create_app(LiveALPR(b))) as mcp:
        # This boundary models Gateway namespace rewriting, not provider forwarding proof.
        def rpc(method, params, **kwargs):
            if method == 'initialize': return {'capabilities': {'tools': {}}}
            if method == 'notifications/initialized': return {}
            headers = {'accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-03-26'}
            params = deepcopy(params)
            if method == 'tools/call':
                assert set(params['arguments']) == {'question'}
                params['name'] = params['name'].split('___')[1]
                headers[CALLER_HEADER] = kwargs['caller_reference']
            response = mcp.post('/mcp', headers=headers, json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).json()
            result = response['result']
            if method == 'tools/list':
                for tool in result['tools']: tool['name'] = 'alpr-investigation-specialists___' + tool['name']
            return result
        monkeypatch.setattr(gateway, 'rpc', rpc)
        receipt = execute(manifest, ARGS['question'], 'gab-' + admitted.invocation['request_id'],
                          model=SimpleNamespace(converse=converse), gateway=gateway)
    assert len(queries) == 4 and receipt['status'] == 'SUCCEEDED'
    assert len(receipt['tool_calls']) == 1
    assert admitted.cap not in json.dumps(receipt) + json.dumps(model_calls)
    with admitted.journey.store.tx() as db:
        assert get(db, runs.PREFIX + digest(admitted.cap))['state'] == 'FINISHED'


def test_aws_dependencies_connects_concrete_run_exchange(monkeypatch):
    from foundation_harness.journey_runtime import aws_dependencies
    monkeypatch.setenv('JOURNEY_ALPR_ENDPOINT', 'https://offline.execute-api.us-west-2.amazonaws.com/internal/journey/alpr')
    session = SimpleNamespace(region_name='us-west-2', client=lambda *args, **kw: 'model')
    monkeypatch.setattr('boto3.Session', lambda **kw: session)
    manifest = {'region': 'us-west-2', 'tools': [{'name': TOOL}],
                'gateway_url': 'https://offline.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp'}
    model, gateway = aws_dependencies(manifest, alpr_run_reference='a' * 43, invocation={'input': 'test'})
    assert model == 'model' and isinstance(gateway.alpr_admission, RunExchange)
    with pytest.raises(Denied): aws_dependencies(manifest)
