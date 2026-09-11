"""Offline signed JWTs and mocked Cognito only; never real user verification."""
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from botocore.exceptions import ClientError

from backend.hosted_auth import SESSION_COOKIE, sha
from tests.test_hosted_auth import ORIGIN, hosted, token, authenticate

PENDING = '__Host-gab_pending'
HEADERS = {'Sec-Fetch-Site': 'same-origin', 'X-Studio-Verification': '1'}


def pending(hosted, monkeypatch, verified=False, **access_overrides):
    app, client, key = hosted
    start = client.get('/auth/login', follow_redirects=False)
    state = parse_qs(urlparse(start.headers['location']).query)['state'][0]
    with app.state.store.tx() as db:
        flow = dict(db.select('oidc_flows', where=[('state_hash', '=', sha(state))]).fetchone())
    identity = {'iss': app.state.hosted_auth.issuer, 'sub': 'subject-a', 'iat': int(time.time()), 'exp': int(time.time()) + 900,
                'aud': 'syntheticclient', 'token_use': 'id', 'nonce': flow['nonce'], 'email': 'member@example.test'}
    if verified is not None: identity['email_verified'] = verified
    access = token(app, key, scope='openid email profile aws.cognito.signin.user.admin', **access_overrides)
    async def exchange(*args, **kwargs):
        return httpx.Response(200, json={'access_token': access, 'id_token': jwt.encode(identity, key, algorithm='RS256')})
    monkeypatch.setattr(httpx.AsyncClient, 'post', exchange)
    response = client.get('/auth/callback?state='+state+'&code=offline', follow_redirects=False)
    return response


class Cognito:
    def __init__(self):
        self.calls = []; self.verified = 'false'; self.email = 'member@example.test'; self.subject = 'subject-a'; self.error = None
        self.delivery = {'AttributeName': 'email', 'DeliveryMedium': 'EMAIL'}
    def get_user(self, **kwargs):
        self.calls.append('get')
        return {'UserAttributes': [{'Name': k, 'Value': v} for k,v in {'sub': self.subject, 'email': self.email, 'email_verified': self.verified}.items()]}
    def get_user_attribute_verification_code(self, **kwargs):
        self.calls.append('send')
        if self.error: raise ClientError({'Error': {'Code': self.error, 'Message': 'DO NOT EXPOSE'}}, 'Send')
        return {'CodeDeliveryDetails': self.delivery}
    def verify_user_attribute(self, **kwargs):
        self.calls.append('verify')
        assert kwargs['Code'] == '123456'
        self.verified = 'true'


def setup(hosted, monkeypatch):
    assert pending(hosted, monkeypatch).status_code == 303
    app, c, _ = hosted
    provider = Cognito()
    monkeypatch.setattr(app.state.hosted_auth, 'cognito', lambda: provider)
    status = c.get('/auth/verification/status', headers=HEADERS)
    assert status.status_code == 200
    c.headers.update({**HEADERS, 'Origin': ORIGIN, 'X-CSRF-Token': status.json()['csrf']})
    return app, c, provider


@pytest.mark.parametrize('verified', [False, None, 'true', 1])
def test_only_boolean_true_mints_direct_session(hosted, monkeypatch, verified):
    r = pending(hosted, monkeypatch, verified)
    assert r.status_code == 303
    app,c,_ = hosted
    assert c.get('/api/me').status_code == 401
    assert c.get('/auth/verification/status', headers=HEADERS).json()['state'] == 'PENDING_EMAIL_VERIFICATION'
    with app.state.store.tx() as db:
        assert db.execute('SELECT COUNT(*) FROM principals').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM grants').fetchone()[0] == 0


def test_pending_membership_without_grants(hosted, monkeypatch):
    assert pending(hosted, monkeypatch, **{'cognito:groups': ['unapproved']}).status_code == 403
    assert hosted[1].get('/auth/verification/status', headers=HEADERS).status_code == 401


def test_challenge_precedence_fixation_and_body_guard(hosted, monkeypatch):
    app,c,key = hosted
    old = authenticate(app,c,key)
    setup(hosted, monkeypatch)
    c.cookies.set(SESSION_COOKIE, old)
    for path in ['/api', '/api/me', '/api/agents', '/api/admin/catalog', '/api/unknown']:
        assert c.post(path, content=b'x'*70000).status_code == 401
    assert c.post('/auth/verification/verify', content=b'x'*70000, headers={'X-CSRF-Token':'bad'}).status_code == 403
    assert c.post('/auth/verification/send', content=b'x'*70000, headers={'Origin':'https://evil.test'}).status_code == 403
    c.cookies.clear()
    for path in ['send','verify']:
        assert c.post('/auth/verification/'+path, content=b'x'*70000).status_code == 401
    c.cookies.set(SESSION_COOKIE,old)
    assert c.get('/api/me').status_code == 401


def test_send_verify_replay_and_no_tokens(hosted, monkeypatch):
    app,c,provider = setup(hosted,monkeypatch)
    raw = c.cookies.get(PENDING)
    assert c.post('/auth/verification/send', json={}).json()['sent'] is True
    assert c.post('/auth/verification/send', json={}).status_code == 429
    result = c.post('/auth/verification/verify', json={'code':'123456'})
    assert result.status_code == 200
    assert 'token' not in result.text
    assert c.get('/api/me').status_code == 200
    c.cookies.set(PENDING,raw)
    assert c.post('/auth/verification/verify',json={'code':'123456'}).status_code == 401
    assert provider.calls == ['get','send','get','verify','get']


@pytest.mark.parametrize('mismatch', ['subject','email'])
def test_current_identity_binding(hosted,monkeypatch,mismatch):
    app,c,provider = setup(hosted,monkeypatch)
    setattr(provider,mismatch,'different')
    assert c.post('/auth/verification/send',json={}).status_code == 401
    assert provider.calls == ['get']


@pytest.mark.parametrize('error', ['CodeDeliveryFailureException','InvalidEmailRoleAccessPolicyException','LimitExceededException','UnexpectedException'])
def test_provider_error_redaction(hosted,monkeypatch,error):
    app,c,provider = setup(hosted,monkeypatch)
    provider.error = error
    r = c.post('/auth/verification/send',json={})
    assert r.status_code == 400 and 'DO NOT EXPOSE' not in r.text and 'sent' not in r.text
    assert (error in r.text) == (error != 'UnexpectedException')


def test_status_browser_contract_and_expiry(hosted,monkeypatch):
    app,c,provider = setup(hosted,monkeypatch)
    assert c.get('/auth/verification/status',headers={'X-Studio-Verification':''}).status_code == 403
    assert c.get('/auth/verification/status',headers={'Sec-Fetch-Site':'same-site'}).status_code == 403
    monkeypatch.setattr('backend.hosted_auth.time.time',lambda: 9999999999)
    assert c.get('/auth/verification/status').status_code == 401
    assert not provider.calls


def test_atomic_attempts_and_consume(hosted,monkeypatch):
    app,c,_ = setup(hosted,monkeypatch)
    store = app.state.hosted_auth.verifications
    key = sha(c.cookies.get(PENDING))
    def reserve(_):
        try: store.reserve(key,'attempts'); return True
        except Exception: return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve,range(20))) == 5
    def consume(_):
        try: store.consume(key); return True
        except Exception: return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(consume,range(8))) == 1


def test_false_post_verification_state_fails_closed(hosted,monkeypatch):
    app,c,provider = setup(hosted,monkeypatch)
    monkeypatch.setattr(provider,'verify_user_attribute',lambda **kwargs: {})
    assert c.post('/auth/verification/verify',json={'code':'123456'}).status_code == 401
    assert c.get('/api/me').status_code == 401


@pytest.mark.parametrize('delivery', [{}, {'AttributeName':'phone_number','DeliveryMedium':'SMS'}])
def test_delivery_requires_explicit_email_confirmation(hosted,monkeypatch,delivery):
    app,c,provider = setup(hosted,monkeypatch)
    provider.delivery = delivery
    result = c.post('/auth/verification/send',json={})
    assert result.status_code == 400 and '"sent":true' not in result.text


def test_pending_cross_site_business_still_unauthorized(hosted,monkeypatch):
    app,c,provider = setup(hosted,monkeypatch)
    assert c.post('/api/agents',content=b'invalid',headers={'Sec-Fetch-Site':'cross-site'}).status_code == 401


def test_expiry_during_provider_verification_cannot_mint(hosted,monkeypatch):
    app,c,provider = setup(hosted,monkeypatch)
    def verify(**kwargs):
        provider.verified = 'true'
        monkeypatch.setattr('backend.hosted_auth.time.time',lambda: 9999999999)
    monkeypatch.setattr(provider,'verify_user_attribute',verify)
    assert c.post('/auth/verification/verify',json={'code':'123456'}).status_code == 401
    with app.state.store.tx() as db:
        assert db.execute('SELECT COUNT(*) FROM hosted_sessions').fetchone()[0] == 0


def test_new_callback_revokes_previous_pending_even_on_failure(hosted,monkeypatch):
    app,c,provider = setup(hosted,monkeypatch)
    cookie = c.cookies.get(PENDING)
    assert c.get('/auth/callback?code=invalid',follow_redirects=False).status_code == 400
    c.cookies.set(PENDING,cookie)
    assert c.get('/auth/verification/status').status_code == 401


def test_send_three_budget_and_five_attempts(hosted,monkeypatch):
    app,c,provider = setup(hosted,monkeypatch)
    now = int(time.time())
    for step in range(3):
        monkeypatch.setattr('backend.hosted_auth.time.time',lambda: now+step*61)
        assert c.post('/auth/verification/send',json={}).status_code == 200
    monkeypatch.setattr('backend.hosted_auth.time.time',lambda: now+190)
    assert c.post('/auth/verification/send',json={}).status_code == 429
    def wrong(**kwargs):
        provider.calls.append('verify')
        raise ClientError({'Error':{'Code':'CodeMismatchException','Message':'secret'}},'Verify')
    monkeypatch.setattr(provider,'verify_user_attribute',wrong)
    for _ in range(5): assert c.post('/auth/verification/verify',json={'code':'123456'}).status_code == 400
    assert c.post('/auth/verification/verify',json={'code':'123456'}).status_code == 429
    assert provider.calls.count('verify') == 5


def test_login_start_revokes_strict_cookie_before_cross_site_callback(hosted,monkeypatch):
    app,c,key = hosted
    old = authenticate(app,c,key)
    c.get('/auth/login',follow_redirects=False)
    with app.state.store.tx() as db:
        assert not db.select('hosted_sessions',where=[('id_hash','=',sha(old))]).fetchone()
    assert pending(hosted,monkeypatch).status_code == 303
    old_pending = c.cookies.get(PENDING)
    c.get('/auth/login',follow_redirects=False)
    c.cookies.set(PENDING,old_pending)
    assert c.get('/auth/verification/status',headers=HEADERS).status_code == 401
