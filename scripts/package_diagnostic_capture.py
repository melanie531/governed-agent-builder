"""Build a separate diagnostic ARM64 ZIP. No product allowlist or approval writer.

Settings bind the reviewed diagnostic manifest, not a synthetic product manifest.
Dependencies must come from the existing hash-locked Foundation wheel install.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import zipfile

from foundation_harness.config import canonical, digest
from scripts.package_foundation import ROOT, SOURCES, dependency_files

DIAGNOSTIC_SOURCES = tuple(n for n in SOURCES if n.startswith('foundation_harness/')) + (
    'foundation_harness/diagnostic_exchange.py', 'runtime/__init__.py',
    'runtime/diagnostic_capture.py')


def package(settings, destination, dependencies):
    from foundation_harness.diagnostic_exchange import DiagnosticExchange
    if not isinstance(settings, dict) or set(settings) != {'region', 'exchange_endpoint', 'manifest_digest'}:
        raise ValueError('EXACT_DIAGNOSTIC_SETTINGS_REQUIRED')
    if settings['region'] != 'us-west-2':
        raise ValueError('EXACT_REGION_REQUIRED')
    # Constructor validates endpoint and digest without fetching credentials/network.
    DiagnosticExchange(None, settings['exchange_endpoint'], settings['manifest_digest'])
    files = {}
    for name in DIAGNOSTIC_SOURCES:
        path = ROOT / name
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise ValueError('SOURCE_SYMLINK_DENIED')
        files[name] = path.read_bytes()
    source = digest({k: hashlib.sha256(v).hexdigest() for k, v in files.items()})
    installed = dependency_files(dependencies)
    if files.keys() & installed.keys():
        raise ValueError('DEPENDENCY_SOURCE_COLLISION')
    files.update(installed)
    files['runtime/capture-settings.json'] = canonical(settings)
    files['main.py'] = b'from runtime.diagnostic_capture import create_app\nif __name__ == "__main__":\n    create_app().run()\n'
    files['package-status.json'] = canonical({'kind': 'diagnostic-capture-only',
        'source_digest': source, 'manifest_digest': settings['manifest_digest'],
        'production_ready': False, 'operator_authority_included': False,
        'linux_execution': 'UNVERIFIED'})
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as out, zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, body in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, body)
    return {'sha256': hashlib.sha256(Path(destination).read_bytes()).hexdigest(),
        'source_digest': source, 'manifest_digest': settings['manifest_digest'],
        'bytes': Path(destination).stat().st_size, 'production_ready': False,
        'linux_execution': 'UNVERIFIED', 'cloud_writes': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings', type=Path, required=True)
    parser.add_argument('--dependencies', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(json.loads(args.settings.read_bytes()), args.output, args.dependencies), indent=2))
