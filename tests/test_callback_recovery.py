"""Synthetic callback recovery and HTTP API v2 adapter regression coverage."""
import time
from http.cookies import SimpleCookie
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse, urlencode

import httpx
import jwt
import pytest
import base64
import hashlib

from backend import serverless
from backend.hosted_auth import FLOW_COOKIE, SESSION_COOKIE
from tests.test_hosted_auth import hosted, ORIGIN, token


def test_old_callback_does_not_destroy_new_flow(hosted):
    _, c, _ = hosted
    first = c.get('/auth/login', follow_redirects=False)
    old = parse_qs(urlparse(first.headers['location']).query)['state'][0]
    second = c.get('/auth/login', follow_redirects=False)
    current = parse_qs(urlparse(second.headers['location']).query)['state'][0]
    rejected = c.get('/auth/callback', params={'state': old, 'code': 'synthetic'}, follow_redirects=False)
    assert rejected.status_code == 400
    assert c.cookies.get(FLOW_COOKIE) == current
    assert 'text/html' in rejected.headers['content-type']
    assert 'Return to Agent Studio' in rejected.text
    assert old not in rejected.text and current not in rejected.text
    assert rejected.headers['cache-control'] == 'no-store'
    assert rejected.headers['referrer-policy'] == 'no-referrer'
    assert c.get('/api/me').status_code == 401


def test_malformed_callback_has_safe_recovery_page(hosted):
    _, c, _ = hosted
    response = c.get('/auth/callback?error=access_denied&error_description=synthetic-private-detail', follow_redirects=False)
    assert response.status_code == 400
    assert 'text/html' in response.headers['content-type']
    assert 'href="/"' in response.text
    assert 'synthetic-private-detail' not in response.text
    assert 'application/json' not in response.headers['content-type']


@pytest.mark.parametrize("failure", [None, "missing-cookie", "mismatch", "missing-state", "missing-code", "nonce"])
def test_http_api_v2_cookie_query_pkce_and_nonce_roundtrip(hosted, monkeypatch, failure):
    app, _, key = hosted
    monkeypatch.setenv('PUBLIC_URL', ORIGIN)
    monkeypatch.setattr(serverless, 'application', lambda: app)
    def invoke(path, query='', cookies=None):
        event = {'version': '2.0', 'routeKey': 'GET ' + path, 'rawPath': path, 'rawQueryString': query,
                 'headers': {'host': 'synthetic.execute-api.example.test', 'x-forwarded-proto': 'https', 'sec-fetch-site': 'cross-site' if 'callback' in path else 'same-origin'},
                 'requestContext': {'http': {'method': 'GET', 'path': path, 'sourceIp': '127.0.0.1'}},
                 'cookies': cookies or [], 'isBase64Encoded': False}
        return serverless.auth_handler(event, SimpleNamespace())
    start = invoke('/auth/login')
    q = parse_qs(urlparse(start['headers']['location']).query)
    jar = SimpleCookie()
    for header in start['cookies']: jar.load(header)
    assert jar[FLOW_COOKIE]['samesite'] == 'lax'
    assert jar[FLOW_COOKIE]['path'] == '/' and jar[FLOW_COOKIE]['secure'] and jar[FLOW_COOKIE]['httponly']
    assert jar[FLOW_COOKIE].value == q['state'][0]
    with app.state.store.tx() as db:
        flow = dict(db.execute('SELECT * FROM oidc_flows').fetchone())
    assert q['code_challenge'][0] == base64.urlsafe_b64encode(hashlib.sha256(flow['verifier'].encode()).digest()).decode().rstrip('=')
    identity = jwt.encode({'iss': app.state.hosted_auth.issuer, 'sub': 'subject-a', 'iat': int(time.time()), 'exp': int(time.time())+900,
        'aud': 'syntheticclient', 'token_use': 'id', 'nonce': 'synthetic-wrong-nonce' if failure == 'nonce' else flow['nonce'], 'email': 'member@example.test', 'email_verified': True}, key, algorithm='RS256')
    async def exchange(self, url, **kwargs):
        assert kwargs['data']['code_verifier'] == flow['verifier']
        assert kwargs['data']['code'] == 'synthetic-code'
        return httpx.Response(200, json={'access_token': token(app, key), 'id_token': identity})
    monkeypatch.setattr(httpx.AsyncClient, 'post', exchange)
    query = urlencode({'state': q['state'][0], 'code': 'synthetic-code'})
    cookie = FLOW_COOKIE + '=' + jar[FLOW_COOKIE].value
    if failure == 'missing-state': query = urlencode({'code': 'synthetic-code'})
    if failure == 'missing-code': query = urlencode({'state': q['state'][0]})
    if failure == 'mismatch': cookie = FLOW_COOKIE + '=synthetic-mismatch'
    result = invoke('/auth/callback', query, [] if failure == 'missing-cookie' else [cookie])
    if failure:
        assert result['statusCode'] == (401 if failure == 'nonce' else 400)
        assert not any(h.startswith(SESSION_COOKIE+'=') and 'Max-Age=0' not in h for h in result['cookies'])
        return
    assert result['statusCode'] == 303
    assert any(h.startswith(SESSION_COOKIE+'=') and 'SameSite=strict' in h for h in result['cookies'])
    assert invoke('/auth/callback', query, [cookie])['statusCode'] == 400
