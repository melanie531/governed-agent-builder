# Knowledge Q&A deployment recovery · 2026-09-14

The owner's existing `My Knowledge Q&A` showed Deployment failed. The previous
acceptance report covered fresh QA agents; its Knowledge Q&A scenarios used
Haiku, while the owner's configuration selected GPT-6 Astra with no dataset.
It did not establish that this existing failed agent had been recovered.

## Failure and recovery

The original deployment failed on `CreateAgentRuntime`. CloudTrail recorded
AccessDenied for the deployed Worker role. No Runtime binding was saved.
The later platform release supplied the Runtime creation permissions, but
terminal failed jobs were not replayed automatically.

The saved v1 definition also pinned an older Foundation artifact.
Retrying the immutable version would retain that artifact, which predates the
GPT-6 opaque-reasoning trace fix. Recovery therefore used the existing version
save operation to create v2 of the same agent using the current published
Foundation. All business-authored fields were verified unchanged:
model, prompt, MCP servers, tool permissions, skills, output format, dataset, and
evaluation threshold. V1 and its failure record were retained.

The scoped operator recovery verified the owner's current Cognito membership
and signed, unexpired Studio authority. It used the ordinary Journey save
service, DynamoDB stream dispatcher, and deployed Worker; it did not create an
administrator role, reset a password, bypass the worker's authorization checks,
or write a synthetic success status. An operator recovery audit event was saved.

## Actual verification

- The same agent's current version is 2; its deployment job is **DEPLOYED**.
- The actual AgentCore Runtime and its DEFAULT endpoint were verified READY.
- Health check invoked `global.openai.gpt-6-astra` and returned a real response.
- Two real IAM-signed Runtime API calls invoked `knowledge___search` through the
  configured AgentCore Gateway. Both answered that Aurora's response target is
  four hours during business days and cited `aurora-support`.
- Versioned S3 execution evidence was read back and validated against the saved
  definition, model, and session.
- Evaluation remains **SKIPPED** because this agent has no dataset.

The owner's browser session expired after deployment. These post-deployment
turns used the native API, not the browser chat endpoint; no browser-chat
acceptance is claimed for this recovery. API turns do not add messages to the
Studio chat history. The agent and its Runtime remain available to the owner.

Resource identifiers, AWS request IDs, and protected operational evidence remain
locally in `artifacts/knowledge-recovery/`; they are not part of this document.

## Regression coverage

The targeted Journey lifecycle, Runtime and cloud-adapter suite passed all
39 tests, including a new recovery regression: CreateAgentRuntime denial,
new Foundation release, same-agent revision, retained configuration and v1
evidence, successful deployment and invocation, and no evaluation without a
dataset.

The hosted runner now explicitly selects GPT-6 Astra for both Knowledge Q&A
dataset scenarios, resolves its ID from the live Catalog, and checks that the
deployed and invoked model match. This updates future coverage; the full
four-scenario hosted suite was not rerun during this incident.
