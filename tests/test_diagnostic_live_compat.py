"""Offline deployed-consumer regressions; all definitions and approvals are synthetic."""
import ast
import hashlib
import json
from pathlib import Path

import pytest

from backend.diagnostic_capture import PREFIX
from backend.foundation_runs import get
from foundation_harness.config import digest
from tests.test_diagnostic_capture import fixture_capture, reviewed
from tests.test_diagnostic_exchange import call, states


def stored_digest(value):
    # Execute the actual producer function without importing app/startup services.
    source = Path(__file__).resolve().parents[1] / 'backend' / 'app.py'
    function = next(node for node in ast.parse(source.read_text()).body
                    if isinstance(node, ast.FunctionDef) and node.name == 'digest')
    namespace = {'hashlib': hashlib, 'json': json}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['digest'](value)


def bind_unicode_definition(f, hash_function=stored_digest):
    """Rebuild only synthetic protected fixture records, never deployment state."""
    with f.store.tx() as db:
        row = db.select('versions', where=[('agent', '=', 'fixture-agent')]).fetchone()
        definition = json.loads(row['body'])
        del definition['digest']
        definition['prompt'] = 'Synthetic café 中文 🦞'
        definition['dataset'] = [{'question': 'naïve résumé', 'answer': '合成'}]
        definition['digest'] = hash_function(definition)
        db.update('versions', {'digest': definition['digest'], 'body': json.dumps(definition)},
                  where=[('agent', '=', 'fixture-agent'), ('version', '=', 1)])
        f.runtime['definition_digest'] = definition['digest']
        f.runtime['readback']['environmentVariables']['DEFINITION_DIGEST'] = definition['digest']
        f.runtime_response['environmentVariables']['DEFINITION_DIGEST'] = definition['digest']
        runtime_ref = reviewed(db, 'runtime', f.runtime)
        pricing = get(db, PREFIX + 'pricing:' + f.authority['pricing_ref'])
        pricing['runtime_ref'] = runtime_ref
        f.authority.update(definition_digest=definition['digest'], runtime_ref=runtime_ref,
                           pricing_ref=reviewed(db, 'pricing', pricing))
        f.ref = reviewed(db, 'authority', f.authority)
    return definition


def version_row(f):
    with f.store.tx() as db:
        return dict(db.select('versions', where=[('agent', '=', 'fixture-agent')]).fetchone())


def assert_denied_without_mutation(f, code):
    before = states(f)
    definition_before = version_row(f)
    with pytest.raises(RuntimeError, match=code):
        call(f)
    assert states(f) == before
    assert version_row(f) == definition_before
    assert f.sends == []


def test_unicode_stored_contract_admitted_without_rewriting_definition(fixture_capture):
    f = fixture_capture
    definition = bind_unicode_definition(f)
    content = {k: v for k, v in definition.items() if k != 'digest'}
    assert definition['digest'] == stored_digest(content)
    assert definition['digest'] != digest(content)
    before = version_row(f)
    # Exercise both compatibility fixes together through the service consumer.
    del f.endpoint_response['targetVersion']
    call(f)
    assert version_row(f) == before
    assert f.sends == []


@pytest.mark.parametrize('fault', ['content', 'embedded_digest', 'stored_digest', 'authority_digest'])
def test_unicode_definition_tamper_denied(fixture_capture, fault):
    f = fixture_capture
    definition = bind_unicode_definition(f)
    with f.store.tx() as db:
        if fault == 'content':
            definition['prompt'] += ' tampered'
        elif fault == 'embedded_digest':
            definition['digest'] = '0' * 64
        elif fault == 'stored_digest':
            db.update('versions', {'digest': '0' * 64}, where=[('agent', '=', 'fixture-agent')])
        else:
            f.authority['definition_digest'] = '0' * 64
            f.ref = reviewed(db, 'authority', f.authority)
        if fault in ('content', 'embedded_digest'):
            db.update('versions', {'body': json.dumps(definition)}, where=[('agent', '=', 'fixture-agent')])
    assert_denied_without_mutation(f, 'CAPTURE_DEFINITION_BINDING_DENIED')


def test_alternative_unicode_hash_is_not_silently_accepted(fixture_capture):
    f = fixture_capture
    bind_unicode_definition(f, digest)
    assert_denied_without_mutation(f, 'CAPTURE_DEFINITION_BINDING_DENIED')


def test_manifest_and_protected_record_canonicalization_unchanged(fixture_capture):
    f = fixture_capture
    manifest = {'synthetic': '配置 café'}
    assert digest(manifest) == hashlib.sha256(json.dumps(
        manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
        allow_nan=False).encode()).hexdigest()
    assert digest(manifest) != stored_digest(manifest)
    f.runtime['manifest_digest'] = digest(manifest)
    f.runtime['readback']['environmentVariables']['MANIFEST_DIGEST'] = digest(manifest)
    f.runtime_response['environmentVariables']['MANIFEST_DIGEST'] = digest(manifest)
    # The Unicode request remains bound with the harness contract, NOT app.digest.
    f.authority['request']['system'] = 'Synthetic système 合成'
    f.authority['request_digest'] = digest(f.authority['request'])
    with f.store.tx() as db:
        pricing = get(db, PREFIX + 'pricing:' + f.authority['pricing_ref'])
        pricing['request_digest'] = f.authority['request_digest']
        f.authority['pricing_ref'] = reviewed(db, 'pricing', pricing)
    bind_unicode_definition(f)
    call(f)
    assert f.sends == []


@pytest.mark.parametrize('target', ['absent', '1'])
def test_ready_exact_live_version_accepts_absent_or_equal_target(fixture_capture, target):
    f = fixture_capture
    if target == 'absent':
        del f.endpoint_response['targetVersion']
    else:
        f.endpoint_response['targetVersion'] = target
    call(f)
    assert f.sends == []


@pytest.mark.parametrize('target', ['2', '', None, 1])
def test_explicit_conflicting_or_invalid_target_denied(fixture_capture, target):
    f = fixture_capture
    f.endpoint_response['targetVersion'] = target
    assert_denied_without_mutation(f, 'CAPTURE_RUNTIME_ENDPOINT_DENIED')


@pytest.mark.parametrize('fault', ['endpoint_state', 'live_version', 'missing_live', 'live_type',
    'endpoint_name', 'endpoint_arn', 'runtime_state', 'runtime_version', 'role', 'manifest'])
def test_absent_target_does_not_relax_other_bindings(fixture_capture, fault):
    f = fixture_capture
    del f.endpoint_response['targetVersion']
    code = 'CAPTURE_RUNTIME_ENDPOINT_DENIED'
    if fault == 'endpoint_state': f.endpoint_response['status'] = 'UPDATING'
    elif fault == 'live_version': f.endpoint_response['liveVersion'] = '2'
    elif fault == 'missing_live': del f.endpoint_response['liveVersion']
    elif fault == 'live_type': f.endpoint_response['liveVersion'] = 1
    elif fault == 'endpoint_name': f.endpoint_response['name'] = 'other'
    elif fault == 'endpoint_arn': f.endpoint_response['agentRuntimeArn'] += '-other'
    else:
        code = 'CAPTURE_RUNTIME_READBACK_DENIED'
        if fault == 'runtime_state': f.runtime_response['status'] = 'UPDATING'
        elif fault == 'runtime_version': f.runtime_response['agentRuntimeVersion'] = '2'
        elif fault == 'role': f.runtime_response['roleArn'] += '-other'
        elif fault == 'manifest': f.runtime_response['environmentVariables']['MANIFEST_DIGEST'] = '0' * 64
    assert_denied_without_mutation(f, code)
