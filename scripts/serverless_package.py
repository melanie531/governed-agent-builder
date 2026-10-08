"""Build a locked Linux ARM64 Python 3.13 Lambda zip; no local artifacts/secrets."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main(qa_enrollments=None, *, output=None):
    output = Path(output) if output is not None else ROOT / "artifacts/serverless-release.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    requirements = output.with_suffix(".requirements.txt")
    subprocess.run(["uv", "export", "--frozen", "--no-dev", "--extra", "foundation-runtime", "--no-emit-project", "--format", "requirements-txt", "--output-file", str(requirements)], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    with tempfile.TemporaryDirectory(prefix="gab-serverless-package-") as temporary:
        target = Path(temporary)
        subprocess.run(["uv", "pip", "install", "--target", str(target), "--python-version", "3.13", "--python-platform", "aarch64-manylinux2014", "--only-binary", ":all:", "--require-hashes", "-r", str(requirements)], cwd=ROOT, check=True)
        shutil.copytree(ROOT / "backend", target / "backend", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
        if qa_enrollments is not None:
            shutil.copy2(qa_enrollments, target / "backend/qa_enrollments.json")
        for name in ('foundation_harness', 'foundations', 'tools'):
            shutil.copytree(ROOT / name, target / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))
        (target / 'scripts').mkdir()
        for name in ('foundation_target.py', 'package_foundation.py', 'verify_package_admission.py',
                     'opus_capture_ticket.py'):
            shutil.copy2(ROOT / 'scripts' / name, target / 'scripts' / name)
        # Approval verifies the exact packaged executor source, not a runtime ARN.
        shutil.copytree(ROOT / 'runtime', target / 'runtime', ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))
        shutil.copy2(ROOT / "uv.lock", target / "uv.lock")
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(target.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    info = zipfile.ZipInfo(str(path.relative_to(target)), date_time=(2026, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = (0o100755 if path.stat().st_mode & 0o111 else 0o100644) << 16
                    archive.writestr(info, path.read_bytes())
    print("Lambda ZIP bytes:", output.stat().st_size)
    print("Lambda ZIP sha256:", hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--qa-enrollments", type=Path, help="Protected, operator-approved temporary QA enrollments")
    parser.add_argument("--output", type=Path, help="Target-specific output ZIP")
    args = parser.parse_args()
    main(args.qa_enrollments, output=args.output)
