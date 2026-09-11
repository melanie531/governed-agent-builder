#!/usr/bin/env python3
"""Build a source ZIP only. No credential access, dependency installation or AWS calls.

Install runtime/web_research/requirements.lock into the extracted root in an
approved Linux ARM64 build environment before uploading a direct-code artifact.
"""
import argparse
import json
from pathlib import Path
import sys
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from foundations.web_research import digest
from runtime.web_research.harness import validate_manifest


def package(manifest, destination):
    validate_manifest(manifest)
    root=Path(__file__).resolve().parents[1]
    # Explicit reviewed source set: never recursively sweep operator files, logs,
    # credentials, generated manifests or unrelated future tools into an export.
    files = [
        'backend/__init__.py', 'backend/schemas.py',
        'foundations/__init__.py', 'foundations/web_research.py',
        'tools/__init__.py', 'tools/web_fetch/__init__.py',
        'tools/web_fetch/handler.py', 'tools/web_fetch/policy.json',
        'tools/web_fetch/tool-schema.json', 'tools/web_fetch/requirements.txt',
        'runtime/__init__.py', 'runtime/web_research/__init__.py',
        'runtime/web_research/app.py', 'runtime/web_research/gateways.py',
        'runtime/web_research/harness.py', 'runtime/web_research/main.py',
        'runtime/web_research/requirements.txt', 'runtime/web_research/requirements.lock',
        'runtime/web_research/Dockerfile', 'docs/WEB-RESEARCH-SOURCE.md',
    ]
    if any((root / name).is_symlink() or any(parent.is_symlink()
           for parent in (root / name).parents if parent != root) for name in files):
        raise ValueError('Source export does not follow symlinks')
    with zipfile.ZipFile(destination,'x',zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            z.writestr(name,(root/name).read_bytes())
        z.writestr('main.py','from runtime.web_research.app import create_app\ncreate_app().run()\n')
        z.writestr('runtime/web_research/manifest.json',json.dumps(manifest,sort_keys=True))
        z.writestr('runtime/web_research/execution.json',json.dumps({'human_approved':False,'manifest_digest':digest(manifest)}))
        z.writestr('SOURCE_ONLY.txt','NOT_CONFIGURED. Dependencies and operator execution policy must be packaged before deployment. No secrets permitted.\n')
    return {'manifest_digest':digest(manifest),'definition_digest':manifest['definition']['digest'],'deployment_ready':False}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    print(json.dumps(package(json.loads(args.manifest.read_text()),args.output)))
