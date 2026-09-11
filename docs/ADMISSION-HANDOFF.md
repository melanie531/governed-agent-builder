# Admission handoff, 2026-09-12

Code: `28c53db6e545f82931fe3fb3b5fe577da6390358` on private
`feat/live-capabilities`. Full exact status: [NIGHTLY-STATUS.md](NIGHTLY-STATUS.md),
Milestones 12–13. No production acceptance claimed.

## Peer Linux QA

Runtime ZIP is local-only: `artifacts/foundation-admission/runtime-final.zip`.
SHA-256 `3821b5ca17b76b1801c225723b45f14b691dacb55bd4c6c572a5e404b1a2ae82`.
Peer needs authorized private file transfer or regeneration from the immutable
manifest; a git fetch alone does NOT deliver ignored artifacts. Validate actual
Linux ARM64 Python 3.13 dependencies and root entry import, not host Python.
Do not call create_app().run() against real services or create a Runtime merely
to substitute for import QA. Save exact platform, digest and import results.

## Cloud acceptance still missing

Six Add-only resources deployed successfully; all existing resources unchanged.
AWS_IAM exchange anonymous/forged-header 403; operator-signed denial 403;
existing Studio 200 and anonymous API 401. Unknown-run branch not independently
identified behind generic ADMISSION_DENIED. Need real authorized workload
SigV4 principal and denial evidence, without synthesizing gateway context.
Existing foundation role only trusts AgentCore and lacks route invoke grant;
no approved workload credential available. Direct-Lambda simulation is only
implicitDeny. Exact role grant and direct-invoke protection need bounded review
before admission; do not broaden role trust to obtain test credentials.

Keep CreateInference Deny, fixture UI, Cognito/users/SSM/data/BPA intact.
No full Runtime creation before real admission. Live catalogs, evaluator/reader,
UI and Browser remain next slice. Backend release and runtime ZIP are different
artifacts; never confuse their hashes.

## 02:30 AEST continuation / Milestone 14

Policy implementation published at `a7c936e85eba4fdd706bb553affb4710a6378624`;
see NIGHTLY-STATUS milestone 14. Full suite 543 passed. Exact-role-only live
ChangeSet was NOT executed because AWS additionally proposes ToolPolicy.Definition
via FoundationRole.Arn dependency. Receipt at private ignored
artifacts/foundation-admission/role-update-proof.json; do not retry prepare or
execute past the persisted blocked state without resolving scope explicitly.
Existing role grant/direct-invoke Deny therefore remain NOT DEPLOYED.

User explicitly permits a bounded isolated admission-only Runtime AFTER actual
Linux artifact validation and narrow IAM review, before successful workload
admission. This replaces the earlier circular "no Runtime before admission"
restriction for that isolated probe ONLY. No general PUBLIC-network relaxation.
No Runtime created; no synthetic service-probe record or isolated probe entry
implemented yet. Peer Linux receipt still absent at this check. Full current
engine ZIP is NOT itself the authorized deterministic no-model probe.
