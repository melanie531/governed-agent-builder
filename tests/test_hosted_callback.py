"""OAuth callback contract using offline tokens and a mocked token endpoint."""
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest

from backend.hosted_auth import FLOW_COOKIE, SESSION_COOKIE
from tests.test_hosted_auth import ORIGIN, hosted, token


@pytest.mark.parametrize('failure', [None, 'nonce', 'subject', 'email', 'access-purpose'])
def test_pkce_callback_session_and_replay(hosted, monkeypatch, failure):
    app, client, key = hosted
    start = client.get('/auth/login', follow_redirects=False)
    q = parse_qs(urlparse(start.headers['location']).query)
    state = q['state'][0]
    with app.state.store.tx() as db:
        flow = dict(db.execute('SELECT * FROM oidc_flows').fetchone())
    id_claims = {'iss': app.state.hosted_auth.issuer, 'sub': 'subject-a', 'iat': int(time.time()), 'exp': int(time.time()) + 900,
                 'aud': 'syntheticclient', 'token_use': 'id', 'nonce': flow['nonce'], 'email': 'member@example.test', 'email_verified': True}
    if failure == 'nonce': id_claims['nonce'] = 'wrong'
    if failure == 'subject': id_claims['sub'] = 'wrong'
    if failure == 'email': id_claims['email'] = ''
    access = token(app, key, token_use='id') if failure == 'access-purpose' else token(app, key)
    identity = jwt.encode(id_claims, key, algorithm='RS256', headers={'kid': 'offline-key'})
    async def exchange(self, url, **kwargs):
        assert url == app.state.hosted_auth.domain + '/oauth2/token'
        assert kwargs['data']['code_verifier'] == flow['verifier']
        assert kwargs['data']['redirect_uri'] == ORIGIN + '/auth/callback'
        return httpx.Response(200, json={'access_token': access, 'id_token': identity})
    monkeypatch.setattr(httpx.AsyncClient, 'post', exchange)
    callback = '/auth/callback?state=' + state + '&code=synthetic-code'
    response = client.get(callback, headers={'Sec-Fetch-Site':'cross-site'}, follow_redirects=False)
    if failure:
        assert response.status_code == 401
        assert client.get('/api/me').status_code == 401
    else:
        assert response.status_code == 303
        assert response.headers['location'] == ORIGIN + '/'
        cookies = response.headers.get_list('set-cookie')
        session = next(x for x in cookies if x.startswith(SESSION_COOKIE + '='))
        assert 'HttpOnly' in session and 'Secure' in session and 'SameSite=strict' in session
        me = client.get('/api/me')
        assert me.status_code == 200
        assert me.json()['persona']['name'] == 'member@example.test'
        assert 'access_token' not in me.text
    assert client.get(callback, follow_redirects=False).status_code == 400
