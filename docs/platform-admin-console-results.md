# Platform administrator acceptance · 2026-09-13

Application: https://d2qosd763k95sb.cloudfront.net/

Worktree: `governed-agent-builder-platform-admin`; branch:
`feat/platform-admin-console`. Product code deployed from `8e97be2`.

## Hosted administrator verification

The final hosted runner completed with real Cognito business and administrator
sessions, without intercepted API responses.

- A business user submitted the short tool name `CRM`. The administrator found
  that same request, saved an In review response, and the user's existing page
  updated automatically.
- Independent DynamoDB readback verified the requester, administrator, status,
  version and both submission/response audit events.
- The business identity received HTTP 403 for administrator operational data.
- Agent overview and CloudWatch model metrics returned real source data. Model
  metrics explicitly cover account-wide usage for the Catalog's model IDs.
- Bedrock discovery returned 151 active model/inference-profile options.
- Cost Explorer returned `NO_DATA` after project-tag activation, with null total
  and no invented daily zeroes. Billing attribution is delayed; untagged model
  inference and shared resources are excluded.

Protected local receipts and screenshots: `artifacts/admin-live/`.

## Real Create Agent journeys

All four journeys deployed an actual AgentCore Runtime, invoked tools through
the platform AgentCore Gateway, completed two chat turns with refreshable
history, and passed the typed-name deletion confirmation and native resource
cleanup checks.

| Template | Dataset | Model | Evaluation |
| --- | --- | --- | --- |
| Research | Synthetic, two cases | GPT-6 Astra | Native AgentCore evaluation passed |
| Knowledge Q&A | Synthetic, two cases | Claude Haiku 4.5 | Native AgentCore evaluation passed |
| Research | Absent | Claude Haiku 4.5 | Skipped; no evaluation cases |
| Knowledge Q&A | Absent | Claude Haiku 4.5 | Skipped; no evaluation cases |

Research also passed a direct signed Runtime API invocation. Native evaluation
request IDs and deletion receipts are retained in the business worktree under
`artifacts/journey-live/`. A long combined test session expired during the third
cleanup; a fresh login completed and verified that cleanup. The runner now starts
each scenario with its own real Cognito session.

## Regression verification

Local browser checks passed all 30 existing scenarios and nine Journey/admin
scenarios, including concurrent administrator drafts. The initial full Python
regression passed 1,176 tests; subsequent focused checks covered the final
Registry integrity, policy and billing fixes.

CI exposed a pre-existing race in Moto's unlocked conditional-update simulator.
The DynamoDB adapter uses a single conditional UpdateItem. A temporary real AWS
table independently verified that only 5 of 20 parallel reservations and 1 of 8
parallel consumes succeeded; the table was deleted. The Moto test now checks the
conditional-write contract sequentially; the separate local-store concurrency
test remains.

## Outstanding account requirement

Native AgentCore Registry creation is blocked in this account. The real AWS
`CreateRegistry` call returned `AccessDeniedException`, even though the operator
role has AdministratorAccess and IAM simulation returned allowed. The console
shows Registry unavailable and does not claim a successful native publication.

Registry registration/approval/publication code and its offline contract tests
are implemented. Native acceptance requires resolving that account-side access
restriction, then rerunning `scripts/platform_admin_configure.py` and the
platform deployment. No IAM workaround was applied.
