"""Build a locked Linux ARM64 Python 3.13 Lambda zip; no local artifacts/secrets."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT / "artifacts/serverless-release.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    requirements = ROOT / "artifacts/serverless-requirements.txt"
    subprocess.run(["uv", "export", "--frozen", "--no-dev", "--no-emit-project", "--format", "requirements-txt", "--output-file", str(requirements)], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    with tempfile.TemporaryDirectory(prefix="gab-serverless-package-") as temporary:
        target = Path(temporary)
        subprocess.run(["uv", "pip", "install", "--target", str(target), "--python-version", "3.13", "--python-platform", "aarch64-manylinux2014", "--only-binary", ":all:", "--require-hashes", "-r", str(requirements)], cwd=ROOT, check=True)
        shutil.copytree(ROOT / "backend", target / "backend", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copy2(ROOT / "uv.lock", target / "uv.lock")
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(target.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    archive.write(path, str(path.relative_to(target)))
    print("Lambda ZIP bytes:", output.stat().st_size)
    print("Lambda ZIP sha256:", hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == "__main__": main()
