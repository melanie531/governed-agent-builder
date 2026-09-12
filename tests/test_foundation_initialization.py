"""Synthetic offline bootstrap tests. Never cloud execution or Linux proof."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import boto3
import pytest
from fastapi import HTTPException
from moto import mock_aws

from backend.dynamo_store import DynamoStore, DynamoUnit
from backend import foundation_runs as runs
from foundation_harness.config import digest
from scripts.initialize_foundation import (
    APP, ARTIFACTS, STACK, CONFIG_KEY, FACT_KEY, Resources, apply, main,
    prepare, read_setting, setting_key, evidence_report,
)

ACCOUNT = '9988' '77665544'
ROLE = f'arn:aws:iam::{ACCOUNT}:role/synthetic-runtime'
WORKER = f'arn:aws:iam::{ACCOUNT}:role/synthetic-worker'


@pytest.fixture
def store():
    with mock_aws():
        resource = boto3.resource('dynamodb', region_name='us-west-2')
        resource.create_table(TableName='synthetic-initialization',
            KeySchema=[{'AttributeName': 'pk', 'KeyType': 'HASH'}, {'AttributeName': 'sk', 'KeyType': 'RANGE'}],
            AttributeDefinitions=[{'AttributeName': k, 'AttributeType': 'S'} for k in ('pk', 'sk')],
            BillingMode='PAY_PER_REQUEST')
        yield DynamoStore('synthetic-initialization', resource)


@pytest.fixture
def facts():
    return {'schema_version': 1, 'account': ACCOUNT, 'region': 'us-west-2',
            'studio_stack': APP, 'table': 'synthetic-initialization',
            'bucket': 'synthetic-artifacts', 'stacks': {'synthetic': {'stack_id': 'synthetic-id'}},
            'runtime_role': {'arn': ROLE, 'role_id': 'synthetic-id'},
            'worker_role': {'arn': WORKER, 'role_id': 'synthetic-worker-id'},
            'exchange_endpoint': 'https://synthetic.execute-api.us-west-2.amazonaws.com/internal/foundation/exchange'}


@pytest.fixture
def config(facts):
    return {'account': ACCOUNT, 'region': 'us-west-2', 'roles': [ROLE],
            'bucket': facts['bucket'], 'producer_role': WORKER,
            'network': {'networkMode': 'VPC', 'networkModeConfig': {
                'subnets': ['subnet-aaaa'], 'securityGroups': ['sg-aaaa']}}}


def plan_for(store, facts, config=None, issues=None):
    return prepare(store.table, facts, config, issues or [], Mock())


def test_correct_composite_key_and_nested_body(store):
    with store.tx() as db:
        runs.put(db, CONFIG_KEY, {'account': ACCOUNT})
    assert setting_key(CONFIG_KEY) == {'pk': 'settings', 'sk': '["foundation-deployment"]'}
    assert read_setting(store.table, CONFIG_KEY) == {'account': ACCOUNT}
    assert store.table.get_item(Key={'pk': 'settings', 'sk': CONFIG_KEY}).get('Item') is None


def test_absent_config_dryrun_actionable_and_zero_writes(store, facts, monkeypatch):
    monkeypatch.setattr(store.table.meta.client, 'transact_write_items',
                        Mock(side_effect=AssertionError('dry-run called a write API')))
    plan = plan_for(store, facts, issues=['EXISTING_VPC_OUTPUTS_REQUIRED'])
    assert plan['deployment_config'] is None
    assert plan['configuration_missing'] == ['EXISTING_VPC_OUTPUTS_REQUIRED']
    assert plan['writes'] == [FACT_KEY]
    assert plan['readiness'] == 'NOT_READY'
    assert all(v == 0 for v in plan['evidence']['record_counts'].values())
    assert 'foundation-linux:RECORD_REQUIRED' in plan['evidence']['missing']
    assert read_setting(store.table, FACT_KEY) is None


def test_facts_only_apply_then_partial_recovery_and_idempotency(store, facts, config):
    with store.tx() as db:
        runs.put(db, 'user-preference', {'theme': 'dark'})
        db.insert('agents', {'id': 'synthetic-agent', 'owner': 'synthetic-user', 'workspace': 'test',
                             'current_version': 1, 'created': 1})
    first = plan_for(store, facts, issues=['EXISTING_VPC_OUTPUTS_REQUIRED'])
    assert apply(store.table, first, digest(first), lambda: first)['status'] == 'INITIALIZED'
    assert read_setting(store.table, CONFIG_KEY) is None
    second = plan_for(store, facts, config)
    apply(store.table, second, digest(second), lambda: second)
    assert read_setting(store.table, CONFIG_KEY) == config
    fresh = plan_for(store, facts, config)
    before = store.table.get_item(Key={'pk': '_revision', 'sk': '_revision'})['Item']
    assert apply(store.table, fresh, digest(fresh), lambda: fresh)['status'] == 'UNCHANGED'
    assert before == store.table.get_item(Key={'pk': '_revision', 'sk': '_revision'})['Item']
    assert read_setting(store.table, 'user-preference') == {'theme': 'dark'}
    assert len(DynamoUnit(store.table).select('agents')) == 1
    assert all(v == 0 for v in fresh['evidence']['record_counts'].values())


def test_preserve_extra_settings_and_existing_config_fields(store, facts, config):
    with store.tx() as db:
        runs.put(db, CONFIG_KEY, {**config, 'reservation_usd': '1', 'evaluation_collector': {'enabled': False}})
        runs.put(db, 'foundation-approved:synthetic', {'untouched': True})
    plan = plan_for(store, facts, config)
    apply(store.table, plan, digest(plan), lambda: plan)
    assert read_setting(store.table, CONFIG_KEY)['reservation_usd'] == '1'
    assert read_setting(store.table, 'foundation-approved:synthetic') == {'untouched': True}


@pytest.mark.parametrize('change', ['config', 'facts', 'provenance', 'schema', 'plan'])
def test_current_config_provenance_and_schema_conflict(store, facts, config, change):
    plan = plan_for(store, facts, config)
    fresh = copy.deepcopy(plan)
    if change in ('config', 'facts'):
        with store.tx() as db:
            runs.put(db, CONFIG_KEY if change == 'config' else FACT_KEY, {'concurrent': True})
    elif change == 'provenance':
        fresh['facts']['runtime_role']['role_id'] = 'recreated-role'
    elif change == 'schema':
        plan['schema_version'] = 2
    with pytest.raises(ValueError):
        apply(store.table, plan, 'wrong' if change == 'plan' else digest(plan), lambda: fresh)
    assert read_setting(store.table, FACT_KEY) in (None, {'concurrent': True})


def test_repository_cas_rejects_concurrent_unrelated_governance(store, facts, config, monkeypatch):
    plan = plan_for(store, facts, config)
    original = DynamoUnit.commit
    def racing(unit):
        other = DynamoUnit(store.table)
        runs.put(other, 'governance-revocation', True)
        original(other)
        original(unit)
    monkeypatch.setattr(DynamoUnit, 'commit', racing)
    with pytest.raises(HTTPException, match='Concurrent governance'):
        apply(store.table, plan, digest(plan), lambda: plan)
    assert read_setting(store.table, FACT_KEY) is None
    assert read_setting(store.table, CONFIG_KEY) is None


def test_legacy_schema_not_overwritten(store, facts, config):
    with store.tx() as db:
        runs.put(db, FACT_KEY, {'schema_version': 900})
    plan = plan_for(store, facts, config)
    with pytest.raises(ValueError, match='UNKNOWN_INITIALIZATION_SCHEMA'):
        apply(store.table, plan, digest(plan), lambda: plan)


@pytest.mark.parametrize('version', ['null', '', None, 'mismatched'])
def test_object_version_blocked_without_minting_evidence(store, facts, version):
    source = {'synthetic-source': True}
    with store.tx() as db:
        runs.put(db, 'foundation-source:synthetic', source)
        runs.put(db, 'foundation-bundle:synthetic', {'bucket': facts['bucket'], 'artifact_key': 'approved/synthetic.zip',
                 'artifact_version': version, 'source_record_digest': digest(source), 'package_digest': 'a' * 64})
    s3 = Mock()
    s3.head_object.return_value = {'VersionId': 'different-version', 'ContentLength': 10}
    with pytest.raises(ValueError, match='OBJECT_VERSION'):
        evidence_report(DynamoUnit(store.table), facts, s3)
    s3.get_object.assert_not_called()
    assert read_setting(store.table, 'foundation-linux:' + 'a' * 64) is None


def test_old_base_pass_is_not_final_binding(store, facts):
    with store.tx() as db:
        runs.put(db, 'foundation-base-linux:' + 'a' * 64,
                 {'status': 'PASS', 'execution': 'ACTUAL_LINUX', 'entrypoint_passed': True})
        runs.put(db, 'foundation-artifact:synthetic', {'definition_digest': 'synthetic', 'package_digest': 'b' * 64})
    report = evidence_report(DynamoUnit(store.table), facts, Mock())
    assert 'CURRENT_FINAL_ARTIFACT_AND_LINUX_REQUIRED' in report['missing']
    assert 'foundation-linux:RECORD_REQUIRED' in report['missing']


def test_sts_mismatch_before_any_other_resource_calls():
    session = Mock()
    session.client.return_value.get_caller_identity.return_value = {'Account': '8877' '66554433'}
    with pytest.raises(ValueError, match='STS_ACCOUNT_MISMATCH'):
        Resources(session, ACCOUNT, 'us-west-2', APP).collect()
    assert [c.args[0] for c in session.client.call_args_list] == ['sts']


@pytest.mark.parametrize('region,stack', [('us-east-1', APP), ('us-west-2', 'old-demo')])
def test_wrong_target_rejected_without_sdk(region, stack):
    session = Mock()
    with pytest.raises(ValueError, match='EXPLICIT_EXISTING_STUDIO_TARGET_REQUIRED'):
        Resources(session, ACCOUNT, region, stack)
    session.client.assert_not_called()


def test_role_provenance_mismatch():
    session = Mock()
    resources = Resources(session, ACCOUNT, 'us-west-2', APP)
    resources.inventory[STACK] = {'FoundationRole': {'PhysicalResourceId': 'synthetic',
        'ResourceType': 'AWS::IAM::Role', 'ResourceStatus': 'CREATE_COMPLETE'}}
    session.client.return_value.get_role.return_value = {'Role': {'Arn': ROLE + '-different'}}
    with pytest.raises(ValueError, match='ROLE_PROVENANCE_MISMATCH'):
        resources.role(STACK, 'FoundationRole', ROLE)


def test_missing_permission_context_is_not_allowed():
    session = Mock()
    session.client.return_value.simulate_principal_policy.return_value = {'EvaluationResults': [
        {'EvalActionName': 'iam:PassRole', 'EvalDecision': 'allowed', 'MissingContextValues': ['aws:SourceArn']}]}
    assert not Resources(session, ACCOUNT, 'us-west-2', APP).permission(WORKER, ['iam:PassRole'], ROLE)


def test_apply_flag_requires_plan_digest_before_sdk():
    with pytest.raises(ValueError, match='APPLY_REQUIRES_CURRENT_PLAN_DIGEST'):
        main(['--expected-account', ACCOUNT, '--profile', 'agentic-platform-prod', '--region', 'us-west-2',
              '--studio-stack', APP, '--apply'])


def test_exact_config_consumed_by_driver(store, facts, config, monkeypatch):
    from backend.foundation_jobs import configured_jobs
    plan = plan_for(store, facts, config)
    apply(store.table, plan, digest(plan), lambda: plan)
    monkeypatch.setenv('FOUNDATION_LIVE_ENABLED', '1')
    monkeypatch.setenv('FOUNDATION_PRODUCER_ENABLED', '1')
    monkeypatch.setattr(boto3, 'Session', Mock())
    driver = configured_jobs(store, worker=True)
    assert driver.deployment.network == config['network']
    assert driver.deployment.adapter.policy.approved_roles == frozenset(config['roles'])
    assert driver.deployment.adapter.policy.artifact_bucket == config['bucket']
    assert driver.producer is not None
    assert driver.artifact_reader is not None
    # Enabling is test-local only; initializer never touches environment/flags.
