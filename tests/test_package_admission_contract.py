import json
from copy import deepcopy
import zipfile
import pytest
from scripts.foundation_probe import example_config
from scripts.package_foundation import package, save_config
from foundation_harness.package_admission import admission_config, validate_admission
from scripts.verify_package_admission import verify_package_admission


def values(tmp_path):
    raw = example_config()
    saved = save_config(raw, tmp_path/'manifests')
    settings = admission_config(raw, 'https://synthetic.execute-api.us-west-2.amazonaws.com/internal/foundation/exchange',
        'arn:aws:iam::'+'9988'+'77665544'+':role/synthetic')
    return raw, saved, settings


def test_live_missing_admission_fails_before_output(tmp_path):
    _, saved, _ = values(tmp_path)
    with pytest.raises(ValueError, match='EXACT_ADMISSION'):
        package(saved, tmp_path/'bad.zip')
    assert not (tmp_path/'bad.zip').exists()


@pytest.mark.parametrize('field,value', [('endpoint','https://evil.invalid'),
    ('endpoint','https://synthetic.execute-api.us-west-2.amazonaws.com/internal/foundation/exchange?token=x'),
    ('manifest_digest','b'*64), ('foundation_digest','b'*64), ('runtime_role','*'),
    ('binding_ref','a'*64), ('credentials',{'synthetic':'value'})])
def test_invalid_cross_manifest_and_credentials_rejected(tmp_path, field, value):
    raw, saved, settings = values(tmp_path)
    settings[field] = value
    with pytest.raises(ValueError):
        package(saved, tmp_path/'bad.zip', admission=settings)
    assert not (tmp_path/'bad.zip').exists()


def test_plausible_other_endpoint_cannot_change_pinned_reference(tmp_path):
    raw, _, settings = values(tmp_path)
    settings['endpoint'] = settings['endpoint'].replace('synthetic.', 'another.')
    with pytest.raises(ValueError, match='REFERENCE_BINDING'):
        validate_admission(settings, raw)


def test_user_supplied_authority_is_not_platform_approval(tmp_path):
    raw, saved, settings = values(tmp_path)
    with pytest.raises(ValueError, match='PLATFORM_OWNED'):
        package(saved, tmp_path/'bad.zip', admission=settings, approved={'authority': True})
    with pytest.raises(ValueError, match='PLATFORM_OWNED'):
        verify_package_admission(raw)


def test_explicit_base_is_labeled_and_exports_no_credentials(tmp_path, monkeypatch):
    raw, saved, settings = values(tmp_path)
    marker = 'synthetic-private-marker-do-not-export'
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', marker)
    result = package(saved, tmp_path/'base.zip', mode='base')
    assert result['package_mode'] == 'base' and not result['deploy_ready']
    with zipfile.ZipFile(tmp_path/'base.zip') as archive:
        assert 'runtime/custom_foundation/admission.json' not in archive.namelist()
        assert json.loads(archive.read('package-status.json'))['mode'] == 'base'
        assert all(marker.encode() not in archive.read(n) for n in archive.namelist())
    with pytest.raises(ValueError, match='BASE_MUST_NOT'):
        package(saved, tmp_path/'mixed.zip', mode='base', admission=settings)
