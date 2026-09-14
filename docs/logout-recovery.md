# Logout recovery

`POST /api/auth/logout` ends the presented Studio session even when its Cognito
token has expired, membership is denied, the session row has expired or the cookie
is missing. API Gateway routes this exact method and path to the Auth Lambda
without the session authorizer. Existing loaded frontend clients use the same URL.

The application requires an approved `Origin` and rejects `Sec-Fetch-Site` values
other than `same-origin` when present. Logout uses this origin check instead of
session CSRF, which may no longer be available. It deletes only the session whose
cookie hash was presented, clears its Secure/HttpOnly cookie, and returns a fixed
Cognito logout URL with the configured Studio return address. Request bodies do
not choose the session, identity or redirect. Other business API routes retain
their authorizer, membership and CSRF checks.

The account menu also offers Sign out when a session check is denied. No automatic
sign-out or redirect occurs on a business authorization error.

Regression coverage includes expired signed tokens, denied memberships, expired
and missing sessions, cookie replay, independent-session isolation, hostile or
missing origins, API Gateway v2 routing, DynamoDB storage and both menu states.
