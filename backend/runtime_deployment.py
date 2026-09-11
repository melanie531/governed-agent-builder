"""Offline-validated AgentCore lifecycle boundary, NOT wired to the fixture worker.

An orchestrator must persist each returned binding and reschedule readiness checks.
No sleep, background Lambda work, fabricated ARN or evaluation success exists here.
"""
from dataclasses import dataclass
import hashlib
import json
import re
from .aws_adapter import IntegrationNotConfigured


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class LiveBudget:
    allow_live: bool = False
    max_calls: int = 0
    max_output_tokens: int = 0

    def validate(self):
        if not self.allow_live or not 1 <= self.max_calls <= 20 or not 1 <= self.max_output_tokens <= 4096:
            raise IntegrationNotConfigured('Explicit live approval and bounded call/token budgets required')


def frozen_manifest(definition, bindings):
    """Every input, tool, skill, dataset and rubric is bound; never execute prose as code."""
    expected = definition.get('digest')
    if not expected or canonical_digest({k: v for k, v in definition.items() if k != 'digest'}) != expected:
        raise ValueError('Definition digest mismatch')
    selected = {definition['model_id'], *definition['tools'], *definition['skills']}
    if selected != set(bindings) or selected != set(definition['component_versions']):
        raise ValueError('Exact selected capability bindings required')
    for key, binding in bindings.items():
        if binding.get('version') != definition['component_versions'][key] or not binding.get('approved'):
            raise ValueError('Unapproved or stale binding')
        if key in definition['tools'] and not binding.get('read_only'):
            raise ValueError('First slice only permits read-only tools')
        if key in definition['skills'] and not binding.get('instruction'):
            raise ValueError('Pinned skill instruction required')
    return {'schema': 'live-harness-manifest-v1', 'definition': definition, 'bindings': bindings,
            'execution_ready': False, 'blocker': 'Gateway transports, runnable artifact and evaluation evidence not integrated'}


@dataclass(frozen=True)
class DeploymentPolicy:
    region: str
    target_account: str
    approved_roles: frozenset[str]
    artifact_bucket: str
    approved_runtime_ids: frozenset[str] = frozenset()
    allow_mutations: bool = False

    def validate(self, role):
        if not self.allow_mutations:
            raise IntegrationNotConfigured('Cloud mutations require explicit exact-target approval')
        if not re.fullmatch(r'\d{12}', self.target_account) or not self.region:
            raise IntegrationNotConfigured('Exact target account and region required')
        if role not in self.approved_roles or not role.startswith(f'arn:aws:iam::{self.target_account}:role/'):
            raise IntegrationNotConfigured('Runtime role is outside the operator PassRole allowlist')
        if not self.artifact_bucket:
            raise IntegrationNotConfigured('Approved versioned artifact bucket required')


class RuntimeDeploymentAdapter:
    def __init__(self, client, policy: DeploymentPolicy):
        # Injected control-plane client only. Construction never discovers credentials.
        self.client, self.policy = client, policy

    def submit(self, *, name, role, artifact_key, artifact_version, definition_digest,
               manifest_digest, runtime_id=None, network_configuration=None):
        self.policy.validate(role)
        if not artifact_version or not artifact_key or not re.fullmatch('[0-9a-f]{64}', definition_digest) or not re.fullmatch('[0-9a-f]{64}', manifest_digest):
            raise ValueError('Immutable artifact version and content digests required')
        if runtime_id and runtime_id not in self.policy.approved_runtime_ids:
            raise IntegrationNotConfigured('Runtime is not in the approved update allowlist')
        # No default public network and no automatic new networking resources.
        if not network_configuration or network_configuration.get('networkMode') != 'VPC':
            raise IntegrationNotConfigured('Operator-approved existing VPC configuration required')
        request = {'agentRuntimeArtifact': {'codeConfiguration': {
            'code': {'s3': {'bucket': self.policy.artifact_bucket, 'prefix': artifact_key, 'versionId': artifact_version}},
            'runtime': 'PYTHON_3_13', 'entryPoint': ['main.py']}},
            'roleArn': role, 'networkConfiguration': network_configuration,
            'environmentVariables': {'DEFINITION_DIGEST': definition_digest, 'MANIFEST_DIGEST': manifest_digest},
            'clientToken': canonical_digest([name, runtime_id, definition_digest, manifest_digest, artifact_version])}
        response = (self.client.update_agent_runtime(agentRuntimeId=runtime_id, **request) if runtime_id
                    else self.client.create_agent_runtime(agentRuntimeName=name, **request))
        arn = response['agentRuntimeArn']
        if not arn.startswith(f'arn:aws:bedrock-agentcore:{self.policy.region}:{self.policy.target_account}:runtime/'):
            raise IntegrationNotConfigured('Runtime response target mismatch')
        version = response['agentRuntimeVersion']
        if not version.isdigit():
            raise IntegrationNotConfigured('An immutable numeric runtime version is required')
        return {'runtime_id': response['agentRuntimeId'], 'runtime_arn': arn, 'runtime_version': version,
                'definition_digest': definition_digest, 'manifest_digest': manifest_digest,
                'stage': 'WAIT_RUNTIME', 'status': response['status']}

    def readiness(self, binding):
        response = self.client.get_agent_runtime(agentRuntimeId=binding['runtime_id'], agentRuntimeVersion=binding['runtime_version'])
        if (response['agentRuntimeVersion'] != binding['runtime_version'] or response['agentRuntimeArn'] != binding['runtime_arn']
                or response.get('environmentVariables', {}).get('DEFINITION_DIGEST') != binding['definition_digest']
                or response.get('environmentVariables', {}).get('MANIFEST_DIGEST') != binding['manifest_digest']):
            raise IntegrationNotConfigured('Runtime immutable binding mismatch')
        status = response['status']
        if status in ('CREATE_FAILED', 'UPDATE_FAILED', 'DELETING'):
            raise IntegrationNotConfigured('Runtime unavailable; inspect operator diagnostics')
        return {**binding, 'status': status, 'stage': 'INVOKE' if status == 'READY' else 'WAIT_RUNTIME'}


def require_live_evidence(evidence, definition, runtime_version):
    """No deterministic/fixture result may masquerade as a managed live judge."""
    if (not evidence or evidence.get('mode') != 'live' or evidence.get('definition_digest') != definition['digest']
            or evidence.get('dataset_ref') != definition['dataset_ref']
            or evidence.get('rubric_ref') != definition['rubric_ref']
            or evidence.get('runtime_version') != runtime_version
            or not evidence.get('trace_reference') or not evidence.get('judge_reference')):
        raise IntegrationNotConfigured('Missing or mismatched live trace/judge evidence; release blocked')
    # This checks bindings only. Evaluation adapters must authenticate/retrieve evidence.
    return {'binding_valid': True, 'passed': False, 'reason': 'Judge adapter verification still required'}
