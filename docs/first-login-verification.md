# First-login email verification

The cloud-hosted application remains a synthetic fixture demo. This change does not enable AgentCore or live model execution.

After Cognito password setup, the authorization-code callback validates PKCE flow, nonce, JWT signatures, issuer, audience/client, subject, expiry, email, and exactly one approved group. Only the boolean `email_verified: true` creates a business session directly. Otherwise the browser receives a random `__Host-gab_pending` cookie (Secure, HttpOnly, SameSite=Strict), bounded by both tokens' expiry and ten minutes. No principal or grants are created for pending users. Login start and callback revoke previous application sessions/challenges; login start handles Strict cookies that are absent on the cross-site callback.

Pending access tokens live only in a separate encrypted DynamoDB Verification table. It has a top-level `expires` TTL, no stream, no backups, and item permissions only for Auth Lambda. Business, Worker, Dispatcher, and Authorizer roles cannot read it. A separate SQLite adapter supports offline tests. OTPs are never persisted; tokens are never returned or logged.

`GET /auth/verification/status` requires the pending cookie, valid JWT, `Sec-Fetch-Site: same-origin`, and `X-Studio-Verification: 1`. Both POST routes authenticate the challenge and enforce exact Origin and CSRF before body parsing. The browser cannot select a subject, email, or access token. Per-record conditional operations reserve at most three sends with a 60-second cooldown and five code attempts before Cognito I/O. Provider failures also spend their reserved budget.

Each send/verify first calls token-authenticated GetUser and binds its current subject/email to the validated identity. Delivery is announced only after Cognito returns email CodeDeliveryDetails. Verification requires VerifyUserAttribute success, another matching GetUser with the string `email_verified == 'true'`, atomic one-time consumption, and JWT/expiry revalidation before creating a business session. Errors are generic except allowlisted service error names useful for delivery/configuration troubleshooting. A failure after consumption requires signing in again.

A pending cookie overrides a business-session cookie. The API Gateway authorizer returns a denial marker for pending cookies, and Business Lambda returns 401 immediately. It grants no subject or business authority. Middleware independently rejects pending cookies before parsing business bodies. Missing gateway authority also returns 401.

The new stack's public client adds `aws.cognito.signin.user.admin`; the old identity template remains unchanged. Cognito's token APIs do not authorize via IAM, so no Cognito IAM actions were added. References:

- [GetUserAttributeVerificationCode](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_GetUserAttributeVerificationCode.html)
- [VerifyUserAttribute](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_VerifyUserAttribute.html)
- [GetUser](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_GetUser.html)

## Validation and deployment

Baseline: 149 backend tests passed; final suite: 178 passed. Offline tests use signed synthetic JWTs, mocked Cognito, SQLite and Moto DynamoDB; browser tests intercept authentication responses. They are not evidence of human login or email delivery. The UI build passed and all six Chrome browser tests passed, including pending completion, failure without a false delivery indicator, expiry, and the session-expired event.

Deployment uses the `verification-deploy` command in `scripts/serverless_deploy.py`, not the unrelated EC2 preflight or direct stack update. It checks ownership internally, compares the entire prior template, evaluates an UPDATE change set, forbids removals/replacements/Pool changes, and permits only the intended auth changes plus unchanged Lambda ARN dependency references. Existing data and the user pool are preserved.

Live readback passed: stack UPDATE_COMPLETE; all five functions (Auth, Business, Authorizer, Worker, Dispatcher) match the local ZIP by Lambda CodeSha256 and independently downloaded ZIP/source bytes. All backend Python file hashes matched. ZIP SHA-256: `fd2d1f826de8d3426b4587f8c4bdbf0fe5fc5f39768b424c3131ff0ec5903cdf`. Lambda base64 CodeSha256: `/S0fgm3o00JrRYf4xL2/D+X8Xzl2i0JMMTH/DsWQPN8=`.

Changed backend source SHA-256 values:

- `backend/hosted_auth.py`: `c020cfc4470f94079a002a5cce638dcc91d9aacd252ade014082002f0e53e6d1`
- `backend/verification_store.py`: `00a4b61df96581a285a65cf2a3a81609c8176bef141df41c8278d879d82b39f3`
- `backend/app.py`: `eaae8eeb0a6775d4e0d53ad7592f7bd4bd56b5517c1de2d90c6a364994a98992`
- `backend/serverless.py`: `99f1f192708c5aef25b7a6dad677d0c10836a311373aaa7a027f4ce1779203fc`

Readback confirmed unchanged Pool configuration/identity and client callback/logout URLs, the added client scope, active encrypted Verification table with TTL enabled and no stream/backups, actual Auth-only table IAM access, and auth routes/Lambda permissions. The published index matched the local build. Public login returned scoped PKCE S256; verification status and both POST routes returned 401 without a cookie, including invalid POST bodies. GET/POST smoke checks of `/api`, me, agents, admin catalog, jobs, exports, and unknown API paths all returned 401. Pending-cookie enforcement is covered offline; no pending user session was created in the cloud.

## Pending human acceptance and limits

Open https://de32ssfw7gsad.cloudfront.net, select **Sign in / Open Studio**, and use the credentials already stored securely. Complete any first-password setup, select **Send verification code**, and type the emailed code only into the UI. Successful verification should load the workspace. Do not send credentials or OTPs to an agent.

No real user login, send-code, verify-code, reset, AdminGetUser, or email_verified override was performed during this work. Email delivery, service quota, and the complete human first-login journey remain untested. The UI accurately reports provider failure and permits restart after expiry. DynamoDB TTL physical deletion is asynchronous; application expiry checks remain authoritative. Existing membership/session behavior remains bounded by JWT lifetime. The remote repository has no main branch; the required main comparison was attempted and its absence confirmed, with push safety instead checked against origin/feat/local-first.
