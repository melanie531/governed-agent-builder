from copy import deepcopy
import json
import pytest

from backend.foundation_runs import get
from foundation_harness.config import digest
from foundation_harness.context import Denied
from infra.serverless import template
from scripts.journey_alpr_release import prepare_template, register
from tests.test_journey_alpr_admission import setup
from tests.test_journey_alpr_run import admitted


def test_additive_template_preserves_existing_resources_and_limits_exchange(admitted):
    original = template()
    original['Resources']['ExistingOptionalFoundation'] = {'Type': 'Custom::Preserved', 'Properties': {'Value': 'keep'}}
    result = prepare_template(original, {'account': '123456789012', 'region': 'us-west-2'}, admitted.record)
    for key, value in original['Resources'].items():
        if key not in {'BusinessRole', 'WorkerRole'}: assert result['Resources'][key] == value
    resources = result['Resources']
    assert resources['JourneyALPRExchangeRoute']['Properties']['AuthorizationType'] == 'AWS_IAM'
    assert resources['JourneyALPRExchangePermission']['Properties']['SourceArn']['Fn::Sub'].endswith('/$default/POST/internal/journey/alpr')
    permissions = json.dumps(resources['JourneyALPRExchangeRole'])
    assert not any(action in permissions for action in ('ssm:', 'kms:', 'bedrock:InvokeModel', 'lambda:InvokeFunction', 'iam:PassRole'))
    assert 'GetAgentRuntime' in permissions and 'iam:GetRole' in permissions
    assert prepare_template(result, {'account': '123456789012', 'region': 'us-west-2'}, admitted.record) == result
    assert 'JourneyALPRExchange' not in original['Resources']


def test_registration_binds_manifest_and_never_overwrites_role(admitted):
    with admitted.journey.store.tx() as db:
        state = get(db, 'journey-job:' + admitted.bound.run_ref)
        manifest = get(db, 'journey-manifest:' + state['definition_digest'])
        record = deepcopy(admitted.record)
        location = {'digest': digest(manifest), 'bucket': 'test', 'key': 'manifest', 'version_id': '1'}
        record['a']['configuration']['environmentVariables']['JOURNEY_MANIFEST'] = json.dumps(location)
        artifact = manifest['artifact']
        record['a']['configuration']['agentRuntimeArtifact'] = {'codeConfiguration': {'code': {'s3': {
            'bucket': artifact['bucket'], 'prefix': artifact['key'], 'versionId': artifact['version_id']}},
            'runtime': 'PYTHON_3_13', 'entryPoint': ['main.py']}}
        ref, settings = register(db, admitted.journey.settings, record, manifest)
        assert settings['alpr_deployments'][digest(manifest)]['alpr_deployment'] == ref
        assert get(db, 'journey-alpr-deployment:' + ref) == record
        changed = deepcopy(record); changed['a']['version'] = '2'
        with pytest.raises(Denied, match='ROLE_ALREADY_BOUND'): register(db, settings, changed, manifest)


@pytest.mark.parametrize('account,region', [('999999999999', 'us-west-2'), ('123456789012', 'us-east-1')])
def test_infra_rejects_wrong_target(admitted, account, region):
    with pytest.raises(ValueError): prepare_template(template(), {'account': account, 'region': region}, admitted.record)


@pytest.mark.parametrize('profile,region', [
    ('platform-dev-takeover', 'us-west-2'), ('default', 'us-west-2'), ('nvidia', 'us-east-1')])
def test_main_rejects_unapproved_profile_or_region_before_any_session(monkeypatch, profile, region):
    import scripts.journey_alpr_release as release
    monkeypatch.setattr(release, 'DeploymentTarget',
                        lambda *a, **k: pytest.fail('AWS session must not be created for an unapproved target'))
    monkeypatch.setattr('sys.argv', ['journey_alpr_release', 'prepare',
                        '--expected-account', '123456789012', '--profile', profile, '--region', region,
                        '--state', '/tmp/none.json', '--runtime-a', 'a', '--runtime-b', 'b',
                        '--output', '/tmp/none-out', '--release-key', 'releases/' + '0' * 64 + '/lambda.zip',
                        '--release-version', 'v1'])
    with pytest.raises(Denied, match='ALPR_APPROVED_TARGET_REQUIRED'):
        release.main()
    assert ('nvidia', 'us-west-2') in release.APPROVED_TARGETS
