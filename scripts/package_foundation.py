"""Save immutable configuration before deployment; build deterministic source ZIP."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import zipfile

from foundation_harness.config import canonical, digest, load_config

ROOT = Path(__file__).resolve().parents[1]
SOURCES = tuple('foundation_harness/' + name + '.py' for name in (
    '__init__', 'config', 'context', 'admission', 'skills', 'budget', 'transport',
    'model_client', 'tool_client', 'telemetry', 'engine')) + (
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


def package(saved, destination):
    path = Path(saved)
    raw = json.loads(path.read_bytes())
    load_config(raw, path.stem)
    source = source_digest()
    if raw['foundation']['digest'] != source:
        raise ValueError('FOUNDATION_SOURCE_DIGEST_MISMATCH')
    files = {name: (ROOT / name).read_bytes() for name in SOURCES}
    files['main.py'] = b'from runtime.custom_foundation.main import create_app\ncreate_app().run()\n'
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
            'artifact_kind': 'source-only; install locked Linux ARM64 dependencies before deployment',
            'runtime_created': False, 'production_ready': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(save_config(json.loads(args.config.read_text()), args.store), args.output)))
