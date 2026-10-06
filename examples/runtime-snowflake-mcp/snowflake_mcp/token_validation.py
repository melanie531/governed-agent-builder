"""Package-owned validation of an opaque Snowflake OAuth token."""
import json
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


urlopen = build_opener(ProxyHandler({}), NoRedirect()).open


def validate_token(settings, token):
    """No business query, credential acquisition, token cache or request logging."""
    try:
        request = Request(f"https://{settings.account}.snowflakecomputing.com/api/v2/statements",
            method="POST", headers={
                "Authorization": "Bearer " + token, "Content-Type": "application/json",
                "Accept": "application/json", "X-Snowflake-Authorization-Token-Type": "OAUTH"},
            data=json.dumps({"statement": "SELECT CURRENT_USER(), CURRENT_ROLE()", "timeout": 5,
                             "role": settings.role, "warehouse": settings.warehouse}).encode())
        with urlopen(request, timeout=8) as response:
            raw = response.read(16385)
            if response.status != 200 or len(raw) > 16384:
                return False
        rows = json.loads(raw).get("data")
        return (isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], list)
                and len(rows[0]) == 2 and isinstance(rows[0][0], str) and bool(rows[0][0])
                and rows[0][1] == settings.role)
    except Exception:
        return False
