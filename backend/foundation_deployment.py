"""Create-only per-agent lifecycle over the existing exact-target/VPC adapter."""
import json
from pathlib import Path

from foundation_harness.config import load_config
from .runtime_deployment import RuntimeDeploymentAdapter, canonical_digest


class FoundationDeployment:
    def __init__(self, client, policy, approved_network):
        self.adapter = RuntimeDeploymentAdapter(client, policy)
        self.network = approved_network

    def submit_new(self, saved_manifest, *, role, artifact_key, artifact_version,
                   artifact_manifest_digest, artifact_source_digest, network, deployment_key):
        path = Path(saved_manifest)
        raw = json.loads(path.read_bytes())
        config = load_config(raw, path.stem)
        if (artifact_manifest_digest != path.stem
                or artifact_source_digest != config.foundation.digest):
            raise ValueError('PACKAGED_CONFIG_BINDING_MISMATCH')
        if (not self.network or network != self.network
                or network.get('networkMode') != 'VPC'
                or not network.get('networkModeConfig', {}).get('subnets')
                or not network.get('networkModeConfig', {}).get('securityGroups')):
            raise ValueError('APPROVED_EXISTING_VPC_BINDING_REQUIRED')
        if not isinstance(deployment_key, str) or not 1 <= len(deployment_key) <= 100:
            raise ValueError('OWNED_DEPLOYMENT_IDEMPOTENCY_KEY_REQUIRED')
        name = 'gab_foundation_' + canonical_digest([deployment_key, path.stem])[:24]
        # Existing adapter pins S3 version, target role/account and SDK clientToken.
        # No existing-runtime argument is exposed by this new-agent path.
        return self.adapter.submit(
            name=name, role=role, artifact_key=artifact_key, artifact_version=artifact_version,
            definition_digest=config.foundation.digest, manifest_digest=path.stem,
            network_configuration=network)

    def readiness(self, binding):
        return self.adapter.readiness(binding)
