# Agent Studio hosted fixture preview

Deployment started 2026-09-11. See `CLOUD-VERIFICATION.md` for observed results;
resource creation is not a claim that authenticated acceptance passed.

## Approved boundary

Browser → CloudFront uses HTTPS and the default AWS domain/certificate. HTTP
frontend requests redirect to HTTPS; API/auth behaviors require HTTPS. CloudFront
→ private EC2 uses HTTP, **explicitly approved on 2026-09-11 for synthetic preview**.
This is an unencrypted private hop, not end-to-end TLS. The instance has no public
IP or SSH ingress; TCP/80 ingress references only CloudFront's service-managed SG
in the new VPC. Cognito authorization, token exchange and JWKS all use HTTPS.

Private S3/OAC serves the Cloudscape frontend. API/auth/config behaviors are
cache-disabled, forward viewer cookies/headers, and never rewrite errors as SPA
HTML. The private nginx listener accepts only the exact CloudFront host and sets
that fixed Host upstream. Uvicorn trusts no proxy headers; `PUBLIC_URL` is an
explicit HTTPS origin. Arbitrary forwarded identity/role/protocol headers are not
used. Access logs are disabled to prevent OAuth query/cookie/token logging.

This is an independent prototype, **not an AWS service or SageMaker Studio Domain**.
The UI says `CLOUD-HOSTED DEMO · Fixture runner, no live LLM`.

## Identity

A new invite-only Cognito pool/public code-PKCE client and groups are isolated
from the old demo. Tokens stay server-side on encrypted storage; browsers receive
Secure/HttpOnly/SameSite opaque cookies. State/nonce/issuer/client/signature/token
purpose and exact redirect are verified. Sessions expire within 15 minutes.
Logout invalidates the server session. No password or token goes through chat.

| Group | Membership |
|---|---|
| `studio-research` | Business member, Research studio |
| `studio-operations` | Business member, Operations desk |
| `studio-admin` | Governance administration, not access to private agent content |

Exactly one approved group is required. No self-registration or default admin.
The verified Cognito subject owns records; the browser cannot choose a persona.
All `/api` routes, including disabled `/api/demo/*`, authenticate before body
parsing/business logic. Unauthenticated requests receive 401; an authenticated
request to a disabled demo route receives 404. Mutations require exact Origin and
session-bound CSRF. Revoked application grants are not restored by fresh login.

**Onboarding:** the operator must confirm the exact invitation recipient and one
group. Create the user only in this new pool and let Cognito email its invitation.
Do not collect passwords, authentication codes, or reuse old app credentials.
Real authenticated browser acceptance requires this invited user's interactive
login; local JWT fixtures and browser interceptions are not that evidence.

## Runtime and durable storage

`infra/compute.py` defines isolated VPC/subnets/NAT, private EC2, scoped IAM,
artifact bucket, retained encrypted gp3 data EBS, and ingress-only SG stack.
`infra/bootstrap.sh` is the dedicated instance's reviewed initial configuration.
It installs Python 3.12/nginx, reads a content-addressed release from its private
bucket, verifies SHA256, and installs locked ARM64 wheels offline with hashes.
TLS egress via NAT is used for OS packages, SSM, JWKS, token exchange and logs.

The bootstrap selects the exact EBS volume by NVMe serial, refuses unknown disk
signatures, formats only an empty volume, then mounts it at `/data`. Persistent
fstab UUID and `RequiresMountsFor=/data`/mountpoint checks prevent silent root-disk
fallback. An unprivileged `studio` user runs one Uvicorn process plus its async
fixture worker. `flock` prevents a second cooperating launcher on the same
volume. systemd restarts failures; code is root-owned and the service can write
only `/data`. No live model, AgentCore, Cognito-admin or IAM permission is granted.

The app's initial hosted config is deliberately absent until the distribution and
new identity exist. Nginx returns 503 and the service remains stopped, rather than
starting with an invented callback or unsafe auth mode.

Explicit config, held in root-only `/etc/studio/runtime.env`:
`HOSTED_PREVIEW=1`, no `DEMO_MODE`, `EXECUTION_MODE=local`, exact HTTPS `PUBLIC_URL`,
`STATE_PATH=/data/state.sqlite` and nonsecret new Cognito output identifiers.
Uvicorn binds `127.0.0.1:5187`, proxy-header trust and access logging disabled.

## Deployment and changes

This deployment is explicit, not attached to CI or git push. The operator runs
`scripts/cloud_deploy.py` with actions in dependency order: `network`, `runtime`,
`edge`, `ingress`, `identity`, `frontend`, `configure`. Each step live-checks STS,
validates its CloudFormation, waits for terminal state, and logs sanitized status.
`bootstrap-status` inspects the dedicated instance through SSM, not SSH.

The release `/tmp/gab-release.tgz` contains backend/infra, a `uv export --frozen
--no-dev --no-emit-project` requirements file, and wheels downloaded for CPython
3.12/manylinux2014_aarch64 with `--only-binary=:all: --require-hashes`. The frontend
is a production `npm run build`. Cloud outputs in `/tmp/governed-agent-builder-
cloud-state.json` are operator-local, not committed. Recover them from stack
outputs if that file is lost. Account identifiers are not hardcoded in templates.

Before any future update: inspect CloudFormation changes, backup data, and check
that no old-demo resources or shared roles are in scope. Instance replacement is
**stop-before-detach**, never an overlapping rollout. Stop `studio`, make/verify
an online backup, stop the old instance, detach only after unmounted/stopped, and
attach the retained volume to the replacement in the **same AZ**. The initial
VolumeAttachment does not make arbitrary replacement updates safe; stage removal
of the old attachment before replacing the instance, then add the attachment to
the replacement. Verify mount UUID, service, DB and job recovery before traffic.

## Backup and restore

A systemd daily timer runs `infra/backup.py` as the unprivileged user. SQLite's
online backup API includes committed WAL data and verifies `PRAGMA integrity_check`.
Seven copies are retained on encrypted EBS, mode 0600 in a 0700 directory. These
contain session material; never export or log rows. **Same-volume backups are not
protection against losing the volume/AZ.** A separate encrypted EBS snapshot should
be taken by the authorized operator before destructive maintenance. Snapshot
lifecycle/cross-AZ disaster recovery is not claimed by this preview.

Restore on the dedicated instance: stop `studio`; retain the current DB and its
WAL/SHM as one quarantine set; use SQLite backup API to restore a verified backup
into a new `/data/state.sqlite`; set studio ownership/mode 0600; start the service.
Never copy only a live main DB while WAL writes continue. Do not replay expired
sessions as a substitute for Cognito login. Validate integrity and application
state. Startup replays unfinished side-effect-free fixture jobs subject to their
persisted deadlines and current authorization.

## Cost and cleanup

Low-traffic planning allowance: approximately **USD 55–70/month**, not a quote or
cap. NAT + its public IPv4 is about USD 36/month before transfer, t4g.small about
USD 12–13, two encrypted gp3 volumes about USD 3; CloudFront/S3/logs/transfer vary.
Single AZ, no HA, no paid model usage. Stopping EC2 does not stop NAT/EBS charges.

Safe teardown, with explicit operator approval:
1. Preserve required data/online backup and optional encrypted snapshot. Stop the
   service before detaching storage. Record retained resource IDs privately.
2. Disable the new CloudFront distribution, wait until deployed, then delete the
   edge stack (distribution before VPC origin). Do not modify managed CloudFront SG.
3. Delete the new ingress stack, then runtime stack (instance/attachment only).
4. Cognito pool is deletion-protected/retained: decide whether to preserve users;
   disable protection only with explicit deletion approval. Delete identity stack,
   then deliberately remove retained pool if authorized.
5. Delete network stack only after CloudFront origin ENIs and instance are gone.
   It removes NAT/EIP/routes/VPC and scoped instance role/profile. Retained data
   EBS, artifact bucket and logs remain billable until explicitly deleted.
6. Frontend bucket is also retained. Remove all versions/delete markers only after
   retention approval, then delete the bucket. Audit tagged orphan resources and
   CloudFormation deletion failures; never touch the previous demo's resources.

## Verification honesty

Python JWT/callback fixtures and two mocked hosted browser tests exercise local
contracts. Two other browser tests run the local application/worker. None prove
real Cognito onboarding. Live public rejection, private-origin topology, actual
Cognito login page and runtime durability can be checked before an invite; the
full create/evaluate/revise/export/invoke/grants journey needs an invited session.
AWS logo branding is separate from functional hosting and must use an authorized
official asset without suggesting this prototype is an AWS-operated service.
