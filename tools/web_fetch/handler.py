"""Read-only Lambda target. Policy is packaged by the operator, never a tool argument."""
import hashlib
import ipaddress
import json
import re
import socket
import ssl
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


class FetchDenied(ValueError):
    pass


def normalize_url(value, hosts=None):
    # Deliberately reject all queries/fragments, credentials, IP literals and ambiguous encodings.
    if not isinstance(value, str) or len(value) > 2048 or re.search(r'[\s\\\x00-\x1f\x7f]', value):
        raise FetchDenied('Invalid public HTTPS URL')
    try:
        u = urlsplit(value)
        host = (u.hostname or '').lower()
        if (u.scheme != 'https' or not host or u.username is not None or u.password is not None
                or u.port not in (None, 443) or '?' in value or '#' in value or '%' in u.netloc
                or u.netloc.endswith(':') or host.endswith('.') or not host.isascii()
                or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}', host)):
            raise ValueError()
        # Reject encoded controls, encoded separators and double decoding in paths.
        if (re.search(r'%(?![0-9a-f]{2})', u.path, re.I)
                or re.search(r'%(?:0[0-9a-f]|1[0-9a-f]|7f|25|2f|5c|3f|23)', u.path, re.I)):
            raise ValueError()
        if hosts is not None and host not in hosts:
            raise ValueError()
        return urlunsplit(('https', host, u.path or '/', '', ''))
    except ValueError:
        raise FetchDenied('URL outside approved public HTTPS policy') from None


def public_addresses(host, resolver=socket.getaddrinfo):
    try:
        rows = resolver(host, 443, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(row[4][0] for row in rows))
        if not addresses:
            raise ValueError()
        for value in addresses:
            ip = ipaddress.ip_address(value)
            if (not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified
                    or getattr(ip, 'ipv4_mapped', None) or getattr(ip, 'sixtofour', None)
                    or getattr(ip, 'teredo', None) or '%' in value
                    or ip in ipaddress.ip_network('64:ff9b::/96')
                    or ip in ipaddress.ip_network('64:ff9b:1::/48')):
                raise ValueError()
        return addresses
    except Exception:
        raise FetchDenied('DNS did not resolve exclusively to public addresses') from None


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = []
        self.in_title = False
        self.text = []
        self.title = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'template', 'noscript', 'svg'):
            self.hidden.append(tag)
        if tag == 'title':
            self.in_title = True

    def handle_endtag(self, tag):
        if self.hidden and tag == self.hidden[-1]:
            self.hidden.pop()
        if tag == 'title':
            self.in_title = False

    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)
            if self.in_title:
                self.title.append(data)


def evidence(url, body, content_type, fetched_at=None):
    text = body.decode('utf-8', errors='replace')
    title = ''
    if content_type == 'text/html':
        parser = TextParser()
        parser.feed(text)
        text = ' '.join(parser.text)
        title = ' '.join(parser.title)[:300]
    text = ' '.join(text.split())
    if not text:
        raise FetchDenied('Empty source text')
    return {'citationID': 'src-' + hashlib.sha256(url.encode()).hexdigest()[:16],
            'requestedURL': url, 'finalURL': url, 'title': title,
            'fetchedAt': fetched_at or datetime.now(timezone.utc).isoformat(),
            'text': text, 'digest': hashlib.sha256(text.encode()).hexdigest()}


def fetch(url, policy, *, resolver=socket.getaddrinfo, pool_factory=None, clock=time.monotonic):
    hosts = policy.get('allowed_hosts', [])
    if (not hosts or policy.get('site_conditions_approved') is not True
            or any(normalize_url('https://' + h) != 'https://' + h + '/' for h in hosts)):
        raise FetchDenied('Operator-approved hosts and site conditions required')
    url = normalize_url(url, hosts)
    u = urlsplit(url)
    started = clock()
    addresses = public_addresses(u.hostname, resolver)
    max_bytes = min(max(int(policy.get('max_bytes', 262144)), 1), 262144)
    timeout = min(max(float(policy.get('timeout_seconds', 10)), 0.1), 15)
    if clock() - started >= timeout:
        raise FetchDenied('Fetch deadline exceeded')
    import urllib3
    factory = pool_factory or urllib3.HTTPSConnectionPool
    # Connect ONLY to the vetted literal IP. urllib3 retains the original TLS SNI and
    # certificate identity separately; no second hostname DNS resolution occurs.
    pool = factory(addresses[0], port=443, server_hostname=u.hostname,
                   assert_hostname=u.hostname, cert_reqs=ssl.CERT_REQUIRED,
                   ssl_minimum_version=ssl.TLSVersion.TLSv1_2, retries=False,
                   timeout=urllib3.Timeout(connect=min(3, timeout), read=timeout, total=timeout))
    response = None
    try:
        response = pool.urlopen('GET', u.path or '/', headers={
            'Host': u.hostname, 'Accept': 'text/html, text/plain',
            'Accept-Encoding': 'identity', 'User-Agent': 'GovernedResearch/1.0'},
            redirect=False, retries=False, preload_content=False)
        if response.status != 200:
            raise FetchDenied('Source refused or redirected; no follow-up request')
        kind = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
        if kind not in ('text/html', 'text/plain') or response.headers.get('Content-Encoding', 'identity') != 'identity':
            raise FetchDenied('Unsupported source representation')
        if int(response.headers.get('Content-Length', '0')) > max_bytes:
            raise FetchDenied('Source body exceeds limit')
        body = bytearray()
        # read1 avoids read(n) waiting for a full chunk on a slow-drip response.
        while True:
            if clock() - started >= timeout:
                raise FetchDenied('Fetch deadline exceeded')
            connection = getattr(response, 'connection', None)
            sock = getattr(connection, 'sock', None)
            if sock is not None:
                sock.settimeout(max(0.01, timeout - (clock() - started)))
            chunk = response.read1(min(8192, max_bytes + 1 - len(body)), decode_content=False)
            if clock() - started >= timeout:
                raise FetchDenied('Fetch deadline exceeded')
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > max_bytes:
                raise FetchDenied('Source body exceeds limit')
        return evidence(url, bytes(body), kind)
    except FetchDenied:
        raise
    except Exception:
        raise FetchDenied('HTTPS fetch failed; no retry or fallback') from None
    finally:
        if response is not None:
            response.close()
        pool.close()


def lambda_handler(event, context):
    try:
        if set(event) != {'url'}:
            raise FetchDenied('Only the URL argument is accepted')
        policy = json.loads(Path(__file__).with_name('policy.json').read_text())
        return fetch(event['url'], policy)
    except Exception:
        # Never echo URL, query, upstream response, credentials or transport exceptions.
        return {'error': 'FETCH_DENIED', 'message': 'Source unavailable under approved fetch policy'}
