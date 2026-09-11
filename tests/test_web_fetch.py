import io
import ssl
import pytest
from tools.web_fetch.handler import FetchDenied, fetch, normalize_url, public_addresses, lambda_handler

PUBLIC = '93.184.216.34'
POLICY = {'allowed_hosts': ['example.com'], 'site_conditions_approved': True}

def resolver(*args, **kwargs):
    return [(None, None, None, None, (PUBLIC, 443))]

class Response:
    status = 200
    headers = {'Content-Type': 'text/html'}
    def __init__(self, body=b'<title>Example</title><style>hidden</style><script>evil()</script><p>Measured 17</p>'):
        self.body = io.BytesIO(body)
        self.closed = False
    def read1(self, n, **kwargs):
        return self.body.read(n)
    def close(self):
        self.closed = True

class Pool:
    def __init__(self, response=None):
        self.response = response or Response()
        self.calls = []
        self.closed = False
    def __call__(self, host, **kwargs):
        self.host, self.kwargs = host, kwargs
        return self
    def urlopen(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response
    def close(self):
        self.closed = True

@pytest.mark.parametrize('url', [
    'http://example.com/', 'https://example.com.evil.test/', 'https://example.com@evil.test/',
    'https://user:pass@example.com/', 'https://127.0.0.1/', 'https://[::1]/',
    'https://[::ffff:127.0.0.1]/', 'https://2130706433/', 'https://0177.0.0.1/',
    'https://example.com:444/', 'https://example.com./', 'https://example.com/?secret=x',
    'https://example.com/#x', 'https://example.com\\@evil.test/', 'https://example.com/%0d%0aX',
    'https://%65xample.com/', 'https://éxample.com/', 'https://example.com/%252fsecret',
    'https://example.com/\nfoo', 'https://example.com/?',
])
def test_url_bypasses(url):
    with pytest.raises(FetchDenied):
        normalize_url(url, ['example.com'])

@pytest.mark.parametrize('ip', ['127.0.0.1', '10.1.2.3', '172.16.1.2', '192.168.1.1', '169.254.169.254',
    '0.0.0.0', '100.64.0.1', '192.0.2.1', '224.0.0.1', '240.0.0.1', '::1', '::', 'fe80::1',
    'fc00::1', 'ff02::1', '2001:db8::1', '::ffff:8.8.8.8', '64:ff9b::808:808', '2002:0808:0808::'])
def test_dns_mixed_results_fail_closed(ip):
    with pytest.raises(FetchDenied):
        public_addresses('example.com', lambda *a, **k: resolver() + [(0,0,0,0,(ip,443))])


def test_pinned_connection_and_tls_no_rebinding():
    calls = []
    def rebinding(*a, **k):
        calls.append(a)
        return resolver() if len(calls) == 1 else [(0,0,0,0,('127.0.0.1',443))]
    pool = Pool()
    result = fetch('https://EXAMPLE.com:443/report', POLICY, resolver=rebinding, pool_factory=pool)
    assert len(calls) == 1 and pool.host == PUBLIC
    assert pool.kwargs['server_hostname'] == pool.kwargs['assert_hostname'] == 'example.com'
    assert pool.kwargs['cert_reqs'] == ssl.CERT_REQUIRED
    assert pool.calls[0][1]['headers']['Host'] == 'example.com'
    assert pool.calls[0][1]['redirect'] is False and pool.calls[0][1]['retries'] is False
    assert set(pool.calls[0][1]['headers']) == {'Host','Accept','Accept-Encoding','User-Agent'}
    assert result['title'] == 'Example' and result['text'] == 'Example Measured 17'
    assert result['requestedURL'] == result['finalURL'] == 'https://example.com/report'
    assert pool.closed and pool.response.closed

@pytest.mark.parametrize('status', [301,302,307,308,401,403,429,500])
def test_no_redirect_or_bot_bypass(status):
    pool = Pool(); pool.response.status = status
    with pytest.raises(FetchDenied):
        fetch('https://example.com/', POLICY, resolver=resolver, pool_factory=pool)
    assert len(pool.calls) == 1

@pytest.mark.parametrize('headers,body', [({'Content-Type':'image/png'},b'abc'),
    ({'Content-Type':'text/plain','Content-Encoding':'gzip'},b'abc'),
    ({'Content-Type':'text/plain','Content-Length':'999999'},b'abc'),
    ({'Content-Type':'text/plain'},b'x'*262145), ({'Content-Type':'text/plain'},b'')])
def test_body_representation_limits(headers, body):
    pool=Pool(Response(body));pool.response.headers=headers
    with pytest.raises(FetchDenied):
        fetch('https://example.com/',POLICY,resolver=resolver,pool_factory=pool)


def test_certificate_failure_no_fallback():
    pool = Pool()
    def fail(*a, **k):
        raise ssl.SSLCertVerificationError('untrusted certificate')
    pool.urlopen = fail
    with pytest.raises(FetchDenied, match='HTTPS fetch failed'):
        fetch('https://example.com/', POLICY, resolver=resolver, pool_factory=pool)
    assert pool.closed


def test_deadline_and_missing_policy():
    ticks=iter([0, 0, 16])
    with pytest.raises(FetchDenied, match='deadline'):
        fetch('https://example.com/',POLICY,resolver=resolver,pool_factory=Pool(),clock=lambda:next(ticks))
    with pytest.raises(FetchDenied):
        fetch('https://example.com/',{},resolver=resolver,pool_factory=Pool())
    assert lambda_handler({'url':'https://example.com/?secret=hidden'},None)['error']=='FETCH_DENIED'


def test_public_ipv6():
    assert public_addresses('example.com', lambda *a, **k: [(0,0,0,0,('2606:4700:4700::1111',443))])

@pytest.mark.parametrize('url', ['https://example.com:/', 'https://example.com/%', 'https://example.com/%zz'])
def test_malformed_port_and_percent_encoding(url):
    with pytest.raises(FetchDenied):
        normalize_url(url, ['example.com'])


def test_ipv6_connection_is_pinned_with_original_tls_identity():
    pool = Pool()
    address = '2606:4700:4700::1111'
    fetch('https://example.com/', POLICY,
          resolver=lambda *a, **k: [(0, 0, 0, 0, (address, 443))], pool_factory=pool)
    assert pool.host == address
    assert pool.kwargs['server_hostname'] == pool.kwargs['assert_hostname'] == 'example.com'
    assert pool.kwargs['cert_reqs'] == ssl.CERT_REQUIRED


def test_empty_dns_and_resolver_failure():
    with pytest.raises(FetchDenied):
        public_addresses('example.com', lambda *a, **k: [])
    def failed(*a, **k):
        raise OSError('synthetic failure')
    with pytest.raises(FetchDenied):
        public_addresses('example.com', failed)
