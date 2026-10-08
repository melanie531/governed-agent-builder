# VPC Runtimes and Australia-only model inference

This guide configures two opt-in settings for one Agent Studio deployment:

- **Agent network**: business-agent and uploaded MCP Runtimes run in your private
  VPC subnets instead of `PUBLIC` mode.
- **Model policy `au`**: agents may use only active, system-defined Australia
  cross-region inference profiles (`au.*`) whose destination models are all in
  `ap-southeast-2` or `ap-southeast-4`. Amazon Nova models are excluded.

Both are off by default. Existing installations keep `PUBLIC` networking and the
`global` model policy until you explicitly apply a change.

Every step marked **cloud step — NOT RUN in this change's validation** touches
AWS. Those steps were not executed when this change was tested; only the
offline test suite was run.

## Prerequisites

- `uv`, Python 3.12 or later, and the AWS CLI v2 with a named profile for the
  target account (`aws sts get-caller-identity --profile <profile>` succeeds).
- A VPC with `enableDnsSupport` and `enableDnsHostnames` on, and at least two
  **private** subnets in AZs that offer `com.amazonaws.<region>.bedrock-agentcore.gateway`.
  Private means no `0.0.0.0/0` route to an internet gateway; NAT or VPC endpoints are fine.
- For `au`: the deployment region can reach the `au.*` profiles you intend to use.

```sh
git clone https://github.com/melanie531/governed-agent-builder.git
cd governed-agent-builder
git checkout feat/vpc-runtime-au-cris
uv sync --group dev --extra foundation-runtime
.venv/bin/python -m pytest -q   # offline; no AWS calls
```

Set the target exactly as in docs/deployment.md (placeholders shown):

```sh
GAB_ACCOUNT=<12-digit-account-id>
GAB_PROFILE=<aws-profile>
GAB_REGION=<aws-region>
GAB_STATE="artifacts/account-${GAB_ACCOUNT}-${GAB_REGION}/release-state.json"
GAB_TARGET=(--expected-account "$GAB_ACCOUNT" --profile "$GAB_PROFILE"
  --region "$GAB_REGION" --state "$GAB_STATE")
```

## Inputs

| Input | Example placeholder |
|---|---|
| VPC | `<vpc-id>` |
| Private subnets (≥2 AZs) | `<private-subnet-a>,<private-subnet-b>` |
| Runtime security group | `<runtime-sg>` (from the network stack below) |
| Model policy | `global` or `au` |

## 1. Runtime security group and private Gateway endpoint

**cloud step — NOT RUN in this change's validation.** This stack is separate
from the application stack. It creates only an egress-only Runtime security
group, and an AgentCore Gateway interface endpoint with private DNS. The
endpoint carries only its own security group, which admits TLS from the Runtime
group.

```sh
.venv/bin/python -m infra.agent_network > artifacts/agent-network.json
aws cloudformation deploy --profile "$GAB_PROFILE" --region "$GAB_REGION" \
  --stack-name governed-agent-builder-agent-network \
  --template-file artifacts/agent-network.json \
  --parameter-overrides VpcId=<vpc-id> SubnetIds=<private-subnet-a>,<private-subnet-b> \
  --tags project=governed-agent-builder auto-delete=no
aws cloudformation describe-stacks --profile "$GAB_PROFILE" --region "$GAB_REGION" \
  --stack-name governed-agent-builder-agent-network \
  --query "Stacks[0].Outputs" --output table
```

Expected: `RuntimeSecurityGroupId` (`sg-…`) and `GatewayEndpointId` (`vpce-…`).
Runtimes also need egress to your MCP endpoints, Bedrock and CloudWatch Logs,
through NAT or the corresponding VPC endpoints in the same subnets.

## 2. Agent network: plan, then apply

**cloud step — NOT RUN in this change's validation.** The default is a
read-only plan:

```sh
.venv/bin/python scripts/configure_agent_network.py --vpc-id <vpc-id> \
  --subnets <private-subnet-a>,<private-subnet-b> --security-groups <runtime-sg> \
  "${GAB_TARGET[@]}"
```

Expected output is JSON with `"network"` (`networkMode: VPC`), `"validations"`,
`"would_write"`, `"before"`/`"after"` (previous and new `agentNetwork` and, when
installed, `journeyPlatform.network`) and `"applied": false`. The plan checks:
- the VPC belongs to the bound account and region;
- `enableDnsSupport` and `enableDnsHostnames` are on (else it prints the
  `aws ec2 modify-vpc-attribute` fix; it never changes the VPC);
- the subnets are in that VPC and span at least two AZs;
- the Gateway PrivateLink service exists in the region and in each subnet's AZ;
- the security groups are in that VPC;
- no subnet routes `0.0.0.0/0` to an `igw-`.

Any failure exits non-zero with the reason, and nothing is written. The plan
never falls back to `PUBLIC`.

Apply the same arguments with `--apply`. The apply records `agentNetwork`. On an
installed platform it also updates `journeyPlatform.network` and the live
`journey-platform` settings together. It refuses if those two already disagree.
Its output adds `"existing_runtimes"` (each uploaded MCP Runtime and its pinned
network); it never retires, deletes or migrates them.

- **Fresh installation:** apply after `serverless_deploy deploy` and before
  `agentcore_prerequisites` and `journey_platform prepare` (docs/deployment.md).
  `agentcore_prerequisites` then also verifies or creates the
  `AWSServiceRoleForBedrockAgentCoreNetwork` service-linked role, and `prepare`
  installs the VPC network.
- **Existing installation:** apply, then run
  `.venv/bin/python -m scripts.agentcore_prerequisites "${GAB_TARGET[@]}"`.

## 3. Model policy: discover, plan, apply

**cloud step — NOT RUN in this change's validation.** List the eligible profiles
and inspect their destinations:

```sh
aws bedrock list-inference-profiles --profile "$GAB_PROFILE" --region "$GAB_REGION" \
  --type-equals SYSTEM_DEFINED \
  --query "inferenceProfileSummaries[?starts_with(inferenceProfileId,'au.') && status=='ACTIVE'].inferenceProfileId"
aws bedrock get-inference-profile --profile "$GAB_PROFILE" --region "$GAB_REGION" \
  --inference-profile-identifier <au-profile-id> --query "models[].modelArn"
```

Every `modelArn` must be in `ap-southeast-2` or `ap-southeast-4`. Studio
enforces this, plus `ACTIVE`, `SYSTEM_DEFINED` and the non-Nova rule, when an
administrator validates or publishes a model.

```sh
.venv/bin/python scripts/configure_model_policy.py --policy au "${GAB_TARGET[@]}"          # plan
.venv/bin/python scripts/configure_model_policy.py --policy au --apply "${GAB_TARGET[@]}"  # write
```

Expected plan: `{"current": "global", "requested": "au", "applied": false, …}`.
After apply, publish `au.*` models from the AI Catalog. Model discovery then
lists only `au.*` profiles, and saving an agent with a `global.*` model fails
with an actionable message.

## Applying to a running installation

Application instances read the platform settings when they start. Instances
that are already running keep the previous settings until AWS replaces them.
Run the audit and confirm the result before relying on a change:

```sh
.venv/bin/python -m scripts.mcp_onboarding_audit --existing \
  --state "$GAB_STATE" --output artifacts/security-after.json
```

## Safe update, recovery and legacy behaviour

- **Re-plan at any time.** Running either script without `--apply` writes
  nothing and shows the current validations.
- **Interrupted apply.** Re-run the same `--apply` command. If the live record
  already holds the new value, the bound state is completed. If live and bound
  state differ in any other way, the script stops. Reconcile them before
  continuing, and never edit the state file to bypass the check.
- **Legacy deployments.** With no `agentNetwork`, installs stay `PUBLIC`. With no
  `model_policy`, the policy is `global`, and earlier agent manifests run
  exactly as before.
- **Revert.** Apply `--policy global`. For the network, apply a different
  validated VPC choice. There is no automatic `PUBLIC` fallback.

### Migrating existing Runtimes (separate manual step, NOT automated)

Settings apply only to Runtimes created **after** the change. Each uploaded MCP
Runtime records its network at creation; older Runtimes were created `PUBLIC`
and are pinned `PUBLIC`. Later checks use the pinned network, so a VPC switch
does not break them. Business-agent Runtimes keep their network and model
policy too. Nothing migrates automatically.

1. Business agents: revise each agent in Studio. Pick an allowed model under
   `au`. Deploy a new version, then retire the previous version.
2. Uploaded MCP servers (listed in `existing_runtimes`): after the apply,
   re-upload each package so a new VPC Runtime is created, then retire the old server.
3. Run the audit. `BusinessAgentNetwork` and `runtime_network_pinned` on each
   `PythonMcp/*` record must pass; `runtime_network_platform` is informational
   (`differs…` until re-uploaded).

A deployed agent whose manifest pins `au` fails closed if its model does not
match that policy: "This agent's model is not allowed under the platform model
policy (au); revise the agent's model." It never substitutes another model.

## Acceptance checklist

- [ ] Offline suite passes on the checked-out branch.
- [ ] Network stack outputs `RuntimeSecurityGroupId` and `GatewayEndpointId`.
- [ ] Network plan shows all validations; `--apply` reports `"applied": true` and `existing_runtimes`.
- [ ] `agentcore_prerequisites` verified `AWSServiceRoleForBedrockAgentCoreNetwork`.
- [ ] `configure_model_policy --policy au --apply` reports `"applied": true`.
- [ ] Model discovery lists only `au.*`; validating a `global.*` model is rejected.
- [ ] A new agent with an `au.*` model deploys and answers; its Runtime shows `networkMode: VPC`.
- [ ] The audit passes, including `BusinessAgentNetwork` and `runtime_network_pinned`.
