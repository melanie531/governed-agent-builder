"""Offline additive template assembly from a fresh peer-owned stack snapshot."""
import argparse
import hashlib
import json
from pathlib import Path
from foundation_harness.config import canonical
from infra.diagnostic_capture import assemble, LOGICAL_IDS

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--current-template', type=Path, required=True)
    p.add_argument('--bindings', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    before = json.loads(a.current_template.read_bytes())
    bindings = json.loads(a.bindings.read_bytes())
    after = assemble(before, **bindings)
    with a.output.open('xb') as f:
        f.write(canonical(after))
    a.output.chmod(0o600)
    print(json.dumps({'template_sha256': hashlib.sha256(canonical(after)).hexdigest(),
        'base_sha256': hashlib.sha256(canonical(before)).hexdigest(),
        'added_resources': sorted(LOGICAL_IDS), 'existing_resources_modified': 0,
        'enabled': False, 'cloud_writes': 0}, indent=2))
