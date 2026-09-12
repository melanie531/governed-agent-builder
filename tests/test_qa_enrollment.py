"""Signed offline Cognito fixtures; not browser or live-identity evidence."""
import json
import time

import pytest

from backend import qa_enrollment
from backend.hosted_auth import SESSION_COOKIE
from tests.test_hosted_auth import hosted, authenticate, token
from tests.test_email_verification import pending


@pytest.fixture
def registry(hosted, monkeypatch, tmp_path):
    app, _, _ = hosted
    path = tmp_path / 'qa.json'
    monkeypatch.setattr(qa_enrollment, 'REGISTRY', path)
    row = dict(subject='subject-a', issuer=app.state.hosted_auth.issuer,
               client='syntheticclient', origin=app.state.hosted_auth.public_url,
               group='studio-research', role='business', workspace='research',
               enabled=True, expires=int(time.time()) + 3600,
               approval='synthetic-owner-approval', enrolled_by='synthetic-admin', enrolled_at=1)
    def save(**changes):
        path.write_text(json.dumps({'enrollments': [{**row, **changes}]}))
    save()
    return save, path


def test_exact_enrollment_mints_unverified_and_isolates_role(hosted, registry, monkeypatch):
    assert pending(hosted, monkeypatch, verified=False).status_code == 303
    app, c, key = hosted
    assert c.cookies.get(SESSION_COOKIE).startswith('qa.')
    me = c.get('/api/me').json()['persona']
    assert (me['id'], me['role'], me['workspace']) == ('subject-a', 'business', 'research')
    assert c.get('/api/admin/catalog', headers={'X-Role': 'admin', 'X-User': 'subject-admin'}).status_code == 403
    assert c.get('/api/demo/personas').status_code == 404


def test_admin_exact_enrollment(hosted, registry, monkeypatch):
    registry[0](group='studio-admin', role='admin', workspace='platform')
    assert pending(hosted, monkeypatch, verified=False, group='studio-admin').status_code == 303
    assert hosted[1].get('/api/admin/catalog').status_code == 200


@pytest.mark.parametrize('changes', [dict(subject='other'), dict(subject='*')])
def test_other_subject_and_wildcard_never_exempt(hosted, registry, monkeypatch, changes):
    registry[0](**changes)
    assert pending(hosted, monkeypatch, verified=False).status_code == 303
    assert hosted[1].get('/api/me').status_code == 401


@pytest.mark.parametrize('changes', [dict(enabled=False), dict(expires=1), dict(issuer='wrong'),
    dict(client='wrong'), dict(origin='https://other.test'), dict(role='admin'), dict(workspace='platform'),
    dict(group='studio-admin'), dict(approval=''), dict(enrolled_by='')])
def test_mismatched_enrollment_denied(hosted, registry, monkeypatch, changes):
    registry[0](**changes)
    assert pending(hosted, monkeypatch, verified=False).status_code == 403
    assert hosted[1].get('/api/me').status_code == 401


@pytest.mark.parametrize('removal', ['disabled', 'deleted', 'file_deleted'])
def test_revocation_blocks_existing_session(hosted, registry, monkeypatch, removal):
    assert pending(hosted, monkeypatch, verified=False).status_code == 303
    assert hosted[1].get('/api/me').status_code == 200
    if removal == 'disabled': registry[0](enabled=False)
    elif removal == 'deleted': registry[1].write_text('{"enrollments": []}')
    else: registry[1].unlink()
    assert hosted[1].get('/api/me').status_code == 403


def test_signed_group_change_cannot_escalate(hosted, registry, monkeypatch):
    assert pending(hosted, monkeypatch, verified=False, group='studio-admin').status_code == 403


def test_signed_subject_mismatch_denied(hosted, registry, monkeypatch):
    assert pending(hosted, monkeypatch, verified=False, subject='other').status_code == 401


def test_cookie_prefix_cannot_be_stripped(hosted, registry, monkeypatch):
    assert pending(hosted, monkeypatch, verified=False).status_code == 303
    c = hosted[1]
    value = c.cookies.get(SESSION_COOKIE)
    c.cookies.clear()
    c.cookies.set(SESSION_COOKIE, value[3:])
    assert c.get('/api/me').status_code == 401


def test_forged_signature_still_denied(hosted, registry):
    from cryptography.hazmat.primitives.asymmetric import rsa
    app, c, _ = hosted
    authenticate(app, c, rsa.generate_private_key(public_exponent=65537, key_size=2048))
    assert c.get('/api/me').status_code == 401
