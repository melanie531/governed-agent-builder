# First-login email verification

This describes the Cognito email-verification boundary shared by the hosted application.

After Cognito password setup, the authorization-code callback validates PKCE flow, nonce, JWT signatures, issuer, audience/client, subject, expiry, email, and exactly one approved group. Only the boolean `email_verified: true` creates a business session directly. Otherwise the browser receives a random `__Host-gab_pending` cookie (Secure, HttpOnly, SameSite=Strict), bounded by both tokens' expiry and ten minutes. No principal or grants are created for pending users. Login start and callback revoke previous application sessions/challenges; login start handles Strict cookies that are absent on the cross-site callback.

Pending access tokens live only in a separate encrypted DynamoDB Verification table. It has a top-level `expires` TTL, no stream, no backups, and item permissions only for Auth Lambda. Business, Worker, Dispatcher, and Authorizer roles cannot read it. A separate SQLite adapter supports offline tests. OTPs are never persisted; tokens are never returned or logged.

`GET /auth/verification/status` requires the pending cookie, valid JWT, `Sec-Fetch-Site: same-origin`, and `X-Studio-Verification: 1`. Both POST routes authenticate the challenge and enforce exact Origin and CSRF before body parsing. The browser cannot select a subject, email, or access token. Per-record conditional operations reserve at most three sends with a 60-second cooldown and five code attempts before Cognito I/O. Provider failures also spend their reserved budget.

Each send/verify first calls token-authenticated GetUser and binds its current subject/email to the validated identity. Delivery is announced only after Cognito returns email CodeDeliveryDetails. Verification requires VerifyUserAttribute success, another matching GetUser with the string `email_verified == 'true'`, atomic one-time consumption, and JWT/expiry revalidation before creating a business session. Errors are generic except allowlisted service error names useful for delivery/configuration troubleshooting. A failure after consumption requires signing in again.

A pending cookie overrides a business-session cookie. The API Gateway authorizer returns a denial marker for pending cookies, and Business Lambda returns 401 immediately. It grants no subject or business authority. Middleware independently rejects pending cookies before parsing business bodies. Missing gateway authority also returns 401.

The new stack's public client adds `aws.cognito.signin.user.admin`; the old identity template remains unchanged. Cognito's token APIs do not authorize via IAM, so no Cognito IAM actions were added. References:

- [GetUserAttributeVerificationCode](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_GetUserAttributeVerificationCode.html)
- [VerifyUserAttribute](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_VerifyUserAttribute.html)
- [GetUser](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_GetUser.html)

## Deployment and verification

Email verification is included in the normal application template. Follow the
[current deployment guide](deployment.md); the old one-time migration command
has been removed. Contract tests cover pending-session isolation, OTP limits and
provider failures. Real first-login acceptance still requires the intended user
to complete the emailed-code flow in their own browser. Never copy tokens or OTPs
into release evidence.
