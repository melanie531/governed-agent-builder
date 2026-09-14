# Recovering an unavailable session

Cognito access tokens currently last 15 minutes. API Gateway returns HTTP 403
when its session authorizer denies a request, including requests with expired or
revoked sessions. An open console can therefore still display an administrator
page after its session has ended.

For a hosted business API response of 403, the frontend checks `/api/me`.
If that check returns 401 or 403, the console displays **Sign in required** with
an explicit **Sign in** action. The existing page and entered values remain
visible until the user navigates. The failed operation is never automatically
retried, and sign-in is never started automatically. Sign-in uses the existing
server `/auth/login` flow; the user can select an assigned admin role afterward.

When `/api/me` succeeds, the original operation denial remains an authorization
error. A failed session probe is not treated as evidence of session expiry.
Concurrent probes share one request, with a five-second timeout. Logout keeps its
independent recovery route.

This changes error recovery in the console. Token validity, grants, role checks
and all backend authorization remain enforced.
