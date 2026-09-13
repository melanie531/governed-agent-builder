# General requests least-privilege delta — NOT APPLIED

Root cause: authenticated business/admin GET /api/general-requests both return 500; current Lambda code includes route and repository entity. Current custom Business CloudWatch log shows dynamodb:Query AccessDeniedException in DynamoUnit._rows('general_requests'). Existing requests endpoints return 200. Not an end-user login failure.

Read current Business role ScopedRuntime inline policy. Proposed delta adds exactly general_requests to each of its existing two ForAllValues:StringEquals/dynamodb:LeadingKeys arrays. Resources, actions, other conditions/statements unchanged; exact equality assertion passed after removing the two added values. Shareable diff redacts Resource values identically on both sides. Full original/proposed docs retained privately for controlled apply and rollback. No IAM write, no model/tool grant.

IaC: infra/serverless.py Business entity list gains only general_requests. Whole-template regression compares generated baseline db7c605 with current template and proves exactly two leading-key additions; all other role/statement/template data unchanged. Negative test proves unapproved_partition and wildcard absent; other roles do not gain general_requests.

Executed: /tmp/gab/.venv/bin/python -m pytest tests/test_general_requests_iam.py tests/test_general_requests.py -q
18 passed, 2 existing warnings (1.23s). API tests cover requester/workspace isolation, admin read/handling, persistence and no automatic grants. These are local/moto tests, NOT proof of actual IAM denial after deployment. Live allowed general_requests and denied unapproved-partition verification must be done after explicit IAM approval. Existing roles cannot successfully read general_requests before this delta is applied.
