"""Save immutable configuration before deployment; build deterministic source ZIP."""
import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
import zipfile

from foundation_harness.config import canonical, digest, load_config

ROOT = Path(__file__).resolve().parents[1]
SOURCES = tuple('foundation_harness/' + name + '.py' for name in (
    '__init__', 'config', 'context', 'admission', 'skills', 'budget', 'transport',
    'model_client', 'tool_client', 'telemetry', 'engine', 'backend_exchange', 'package_admission')) + (
    'runtime/__init__.py', 'runtime/custom_foundation/__init__.py',
    'runtime/custom_foundation/main.py', 'runtime/custom_foundation/requirements.lock')


def source_digest():
    values = {}
    for name in SOURCES:
        path = ROOT / name
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise ValueError('SOURCE_SYMLINK_DENIED')
        values[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest(values)


def save_config(raw, directory):
    raw = json.loads(canonical(raw))
    load_config(raw, digest(raw))
    if raw['foundation']['digest'] != source_digest():
        raise ValueError('FOUNDATION_SOURCE_DIGEST_MISMATCH')
    path = Path(directory) / (digest(raw) + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical(raw)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != data:
            raise ValueError('IMMUTABLE_CONFIG_CONFLICT') from None
    else:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    return path


def dependency_command(target):
    return ['uv', 'pip', 'install', '--target', str(target), '--python-version', '3.13',
            '--python-platform', 'aarch64-manylinux2014', '--only-binary', ':all:',
            '--require-hashes', '--no-deps', '--link-mode', 'copy', '-r',
            str(ROOT / 'runtime/custom_foundation/requirements.lock')]


def dependency_files(directory):
    """Only wheel RECORD-listed files; discard installer-local paths/scripts."""
    import csv
    import io
    root = Path(directory)
    allowed = set()
    for record in root.glob('*.dist-info/RECORD'):
        for row in csv.reader(io.StringIO(record.read_text())):
            name = row[0]
            if '..' not in Path(name).parts and not Path(name).is_absolute():
                allowed.add(name)
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('UNEXPECTED_DEPENDENCY_SYMLINK')
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.as_posix() == '.lock':
            continue
        if relative.parts[0] == 'bin' or '__pycache__' in relative.parts or path.suffix == '.pyc':
            continue
        if relative.name in {'RECORD', 'INSTALLER', 'REQUESTED', 'direct_url.json', 'uv_cache.json'} and relative.parent.name.endswith('.dist-info'):
            continue
        name = relative.as_posix()
        if name not in allowed or name.startswith('.'):
            raise ValueError('UNEXPECTED_DEPENDENCY_FILE')
        result[name] = path.read_bytes()
    required = {'bedrock_agentcore/runtime/app.py', 'pydantic_core/__init__.py',
                'opentelemetry/sdk/trace/__init__.py', 'boto3/__init__.py'}
    if not required <= result.keys():
        raise ValueError('LOCKED_RUNTIME_DEPENDENCIES_REQUIRED')
    return result


def package(saved, destination, *, admission=None, dependencies=None, mode="live", approved=None):
    path = Path(saved)
    raw = json.loads(path.read_bytes())
    load_config(raw, path.stem)
    source = source_digest()
    if raw['foundation']['digest'] != source:
        raise ValueError('FOUNDATION_SOURCE_DIGEST_MISMATCH')
    files = {name: (ROOT / name).read_bytes() for name in SOURCES}
    if mode not in {'live', 'base'}:
        raise ValueError('EXPLICIT_PACKAGE_MODE_REQUIRED')
    if mode == 'base':
        if admission is not None or approved is not None:
            raise ValueError('BASE_MUST_NOT_CLAIM_ADMISSION')
    else:
        from foundation_harness.package_admission import validate_admission
        validate_admission(admission, raw)
        from scripts.verify_package_admission import verify_package_admission
        if verify_package_admission(raw, approved=approved) != admission:
            raise ValueError('PLATFORM_VERIFIED_BINDING_REQUIRED')
        if dependencies is None:
            raise ValueError('LIVE_LOCKED_DEPENDENCIES_REQUIRED')
        files['runtime/custom_foundation/admission.json'] = canonical(admission)
    files['package-status.json'] = canonical({'mode': mode, 'deploy_ready': False,
        'linux_execution': 'UNVERIFIED', 'admission_config_present': mode == 'live'})
    if dependencies is not None:
        installed = dependency_files(dependencies)
        if files.keys() & installed.keys():
            raise ValueError('DEPENDENCY_SOURCE_COLLISION')
        files.update(installed)
    files['main.py'] = b'from runtime.custom_foundation.main import create_app\nif __name__ == \"__main__\":\n    create_app().run()\n'
    files['runtime/custom_foundation/harness.json'] = canonical(raw)
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as output:
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(files.items()):
                info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
    return {'source_digest': source, 'manifest_digest': path.stem,
            'package_digest': hashlib.sha256(Path(destination).read_bytes()).hexdigest(),
            'package_mode': mode, 'admission_config_present': mode == 'live',
            'deploy_ready': False, 'artifact_kind': ('linux-arm64-python3.13-locked' if dependencies is not None else
                              'source-only; install locked Linux ARM64 dependencies before deployment'),
            'runtime_created': False, 'production_ready': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--linux-dependencies', action='store_true')
    parser.add_argument('--mode', choices=['base', 'live'], required=True)
    parser.add_argument('--definition-digest', help='Exact protected platform approval selector; not authority')
    args = parser.parse_args()
    saved = save_config(json.loads(args.config.read_text()), args.store)
    admission = approved = None
    if args.mode == 'live':
        from scripts.verify_package_admission import verify_package_admission, read_platform_approval
        approved = read_platform_approval(args.definition_digest)
        admission = verify_package_admission(json.loads(saved.read_bytes()), approved=approved)
    with tempfile.TemporaryDirectory(prefix='foundation-deps-') as deps:
        if args.linux_dependencies:
            subprocess.run(dependency_command(deps), check=True)
        print(json.dumps(package(saved, args.output, dependencies=deps if args.linux_dependencies else None,
                                 mode=args.mode, admission=admission, approved=approved)))
