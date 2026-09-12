# Foundation initialization: source checkpoint, not E2E

## What is executable now

`python -m scripts.initialize_foundation` is the server-owned initialization producer. It defaults to AWS reads only. `--apply --expected-plan-digest <fresh-digest>` is the explicit future write path, not exercised in this slice. It rebuilds the plan from resource metadata, compares current config and provenance, then writes only `foundation-initialization` and, when complete, `foundation-deployment` through the repository's global revision CAS transaction. No whole-settings replacement or catalog seed is involved. Repeated fresh-plan application is a no-op; a facts-only partial initialization can later reconcile to complete configuration.

The command requires explicit expected account, `agentic-platform-prod`, `us-west-2`, and `governed-agent-builder-serverless-app`. STS must match before any other service call. `StudioTarget.verify()` independently checks the existing Studio CloudFront and Cognito binding. This does not adopt an old demo or create a Runtime.

Only these new paths belong to this change:

- `scripts/initialize_foundation.py`
- `tests/test_foundation_initialization.py`
- `tests/test_foundation_initialization_resources.py`
- `docs/FOUNDATION-INITIALIZATION.md`

Model catalog/source/UI work is separate and untouched.

## Consumer to producer path

1. **Existing consumers:** `backend/foundation_jobs.py::configured_jobs()` reads `settings[foundation-deployment]` and constructs `FoundationDeployment`, `DeploymentPolicy`, `ArtifactReadback`, and optionally `FoundationProducer`. The setting previously had consumers but no verified complete initialization producer.
2. **New producer:** `Resources.collect()` adapts current CloudFormation outputs and stack resource identities into verified facts and the exact driver configuration. `prepare()` merges only those verified driver fields with existing optional cost/evaluation settings, preserving them without claiming they are valid.
3. **New guarded writer:** `apply()` re-reads resources, compares plan/config/facts digests and schema version, and commits through `DynamoUnit`. Concurrent governance changes cancel the entire transaction. No user data, grants, approvals, source registrations, or proof records are modified.
4. **Existing authorization remains mandatory:** platform source registration and foundation-policy approval feed `self_service_admission`; the protected approval feeds bundle composition/finalization; `FoundationJobs` still requires current admission, exact versioned artifact readback, bounded costs, and live evidence. Initialization does not approve any of these.

### DynamoDB physical contract

The setting key is `pk = settings`, `sk = ["foundation-deployment"]` (a compact JSON array, **not** the bare setting name). The Dynamo item `body` contains a JSON repository row with `key` and another JSON `body`. Reads are strongly consistent. Dry-run deliberately avoids `store.tx()`: even that adapter's read-only commit calls `TransactWriteItems`. `prepare()` instead reads a `DynamoUnit` without committing. The later write shares the `_revision` fence with application transactions.

### Exact driver configuration

The prepared deployment setting supplies only actual consumer keys:

```json
{
  "account": "<verified STS/stack account>",
  "region": "us-west-2",
  "roles": ["<verified FoundationRole ARN>"],
  "bucket": "<verified serverless artifacts Releases bucket>",
  "network": {
    "networkMode": "VPC",
    "networkModeConfig": {
      "subnets": ["<existing stack-output subnet>"],
      "securityGroups": ["<existing stack-output group>"]
    }
  },
  "producer_role": "<actual stack-owned WorkerRole ARN>"
}
```

Schema version, immutable role IDs, trust-policy digests, CloudFormation stack/output/resource provenance and exchange endpoint are stored in the **separate facts record**, not fake readiness fields. Existing optional configuration is preserved. Unknown initialization schema versions fail closed.

### IaC output adapter and validation

The current app outputs supply `StateTable`, `ApiEndpoint`, and `WorkerFunction`; the artifacts stack supplies `Bucket`; the foundation stack supplies `FoundationRole`. The adapter also verifies physical ownership through `list_stack_resources`, Worker Lambda role readback, IAM role ID/trust, the Dynamo composite key schema, and API Gateway's actual IAM-authorized exchange route and integration. No environment payload is persisted or printed; only the two live/producer flags are inspected and must remain off.

The future network contract is explicit: the existing foundation stack must output comma-separated `FoundationSubnets` and `FoundationSecurityGroups`. The command never guesses a default VPC or takes arbitrary caller-supplied subnet/role/store values. It checks target ownership, one VPC, available subnets and disabled auto-public-IP assignment. This is **not** route-table, endpoint reachability, or security-group safety proof; those require separate pre-enablement validation.

The artifact store must be the app's bound release bucket, not merely a bucket that happens to exist: stack identity, account/region, versioning, four public-access blocks, nonpublic policy, AES256 encryption, and bucket/object TLS denial are checked. A bounded S3 inventory sample establishes object presence independently from registration. It does not approve any object. Existing protected bundle records, if present, require exact object version and size, SHA-256 content, current source and lock, protected base Linux proof, and complete bundle validation. Final-bound Linux evidence is checked separately; old base PASS/BLOCKED logs are not promoted to final binding evidence.

IAM simulation checks Worker PassRole with its service condition, Worker bucket metadata access, runtime exchange invocation, and VPC-conditioned CreateAgentRuntime when network outputs exist. Deny, missing context, pagination, and unavailable simulation remain blocking. Simulation is not actual execution or complete effective-policy proof (SCP, resource policies, endpoint policies and runtime context still matter).

## Actual read-only findings, 2026-09-12

The command completed against the authorized existing Studio target. No AWS write call or flag change was made.

**Verified real resources:** current Studio/CloudFront binding, state table, private versioned release bucket, Worker role identity, Foundation runtime role identity, IAM exchange route and Lambda integration. Runtime exchange permission simulated as allowed.

**Configuration blockers:**

- `FoundationSubnets` and `FoundationSecurityGroups` are absent.
- Worker PassRole to the resolved Foundation role is not allowed by simulation.
- Worker bucket-metadata permissions are not allowed by simulation.

**S3 inventory is independently nonempty:** the bounded read returned 10 objects and `IsTruncated=true` (223,019,402 bytes in that sample). No object was treated as approved or as Linux evidence. Object keys and cloud identifiers are intentionally not copied into this source document.

**Protected evidence:** zero `foundation-source:`, `foundation-policy:`, `foundation-approved:`, `foundation-bundle:`, `foundation-artifact:`, `foundation-linux:`, and `foundation-base-linux:` records. This is not a claim that the S3 bucket is empty, nor that any unregistered object is deployable. There is no identified approved source/bundle or final Linux evidence to bind.

Consequently the current generated plan writes **only verified initialization facts**. It does not write an incomplete `foundation-deployment` setting. Both execution flags remain off. `deployment_driver_missing` is not claimed cleared in the deployed Studio; the source configuration-writer gap now has an executable path, but its legitimate resource dependencies are still missing.

## Next exact action and safe application

Run from the repository, replacing the non-secret account placeholder with the authorized Studio account:

```sh
.venv/bin/python -m scripts.initialize_foundation \
  --expected-account <STUDIO_ACCOUNT_ID> \
  --profile agentic-platform-prod \
  --region us-west-2 \
  --studio-stack governed-agent-builder-serverless-app
```

This is the next executable initialization action, **not a deployment**. Review the returned plan digest. Once AWS writes are separately in scope, the exact facts/config transaction command is:

```sh
.venv/bin/python -m scripts.initialize_foundation \
  --expected-account <STUDIO_ACCOUNT_ID> \
  --profile agentic-platform-prod \
  --region us-west-2 \
  --studio-stack governed-agent-builder-serverless-app \
  --apply --expected-plan-digest <DIGEST_FROM_FRESH_DRY_RUN>
```

A resource/config change invalidates the digest. Re-run dry-run instead of forcing. Apply never consumes a caller-edited plan file. Rerunning after success yields `UNCHANGED`; after partial facts-only application, newly verified dependencies allow reconciliation. No new human foundation approval is invented by this command: platform approval controls remain exactly where the existing application enforces them.

**There is no safe exact CloudFormation execute command yet.** No reviewed network/role change set exists in this slice. Do not run the old M0 `foundation_cloud execute` or toggle flags as a substitute. The next deployment action requires an isolated IaC change set supplying the missing existing-VPC outputs and exact least-privilege package/worker policies, with replacements/deletions and unrelated model resources excluded. The initializer identifies these gaps but deliberately does not mutate IAM or provision networking.

Before flags can change, actual execution must establish:

1. Existing private VPC routes/endpoints/security groups support the runtime, artifact retrieval, exchange and required gateways without unintended public exposure.
2. A dedicated immutable package role, scoped trust, exact versioned object read permissions, Worker PassRole/create/read/endpoint/invoke permissions, producer conditional-write permissions, and effective resource/endpoint/SCP policies are valid. The resolved M0 Foundation role is not proof of a dedicated role per immutable package.
3. Current platform source registration and foundation-policy approval exist; source/catalog bindings and immutable domain admission pass existing controls. No arbitrary implementation authorization counts as source approval.
4. A real locked Linux ARM64 package has an independently protected validator receipt. Approved base composition and final-bound execution evidence are distinct. A placeholder manifest or old startup BLOCKED log is not evidence.
5. Current all-service cost reservation and evaluation collector/evidence exporter configuration are independently verified. The initializer never invents pricing, an approval boolean, a judge PASS, or Linux PASS.
6. Exact S3 object readback, Runtime creation, immutable version/DEFAULT endpoint binding, IAM exchange and model/tool/evaluation paths pass actual bounded E2E execution. None were attempted here.

## Validation and Well-Architected status

**Results:** 147 tests passed across both new initializer suites and existing foundation wiring, approval, finalization, producer and endpoint-admission suites. The new suites contain 33 tests. Two pre-existing Starlette/httpx/AnyIO deprecation warnings remain. No cloud execution or Linux test was run.

Dedicated tests cover correct composite keys, no-write absent-config dry-run, target/role/object-version denial, zero-evidence not-ready state, exact driver construction, idempotency, partial recovery, preservation, current-config/provenance/schema comparison, global CAS conflicts and old-base-proof rejection. All test identities and proofs are synthetic.

- **Security:** fail-closed target/provenance/private store checks; no secret reads, IAM/flag changes or minted authority. Effective IAM and network review still pending.
- **Reliability:** atomic CAS, fresh comparison, no-op retries and partial recovery; actual cloud execution unverified.
- **Performance:** bounded object size/listing and SDK timeouts; no polling or model calls. Settings scans retain the existing low-throughput repository design.
- **Cost:** metadata-only inspection; no Runtime, build, inference or resource creation. All-service cost envelope remains a gate.
- **Operational excellence:** actionable dry-run, explicit apply digest, documented recovery; no E2E success claim.
- **Sustainability:** reuse existing resources; no duplicate infrastructure or idle Runtime provisioned.
