"""Build an uploadable Python 3.13/Linux ARM64 ZIP with all dependencies."""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent


def build(destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="complete-mcp-") as directory:
        work = Path(directory)
        requirements, target = work / "requirements.txt", work / "package"
        subprocess.run(["uv", "export", "--locked", "--no-dev", "--no-emit-project",
                        "--output-file", str(requirements)], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["uv", "pip", "install", "--target", str(target), "--python-version", "3.13",
                        "--python-platform", "aarch64-manylinux2014", "--only-binary", ":all:",
                        "--require-hashes", "-r", str(requirements)], check=True)
        for name in ("main.py", "package_tools.py", "package-marker.txt"):
            shutil.copy2(ROOT / name, target / name)
        shutil.copy2(requirements, target / "requirements.txt")
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(target.rglob("*")):
                relative = path.relative_to(target)
                if (not path.is_file() or "__pycache__" in relative.parts or relative.parts[0] == "bin"
                        or relative.name in {".lock", "INSTALLER", "RECORD", "REQUESTED", "uv_cache.json", "direct_url.json"}):
                    continue
                info = zipfile.ZipInfo(relative.as_posix(), (2026, 1, 1, 0, 0, 0))
                info.compress_type, info.external_attr = zipfile.ZIP_DEFLATED, 0o100644 << 16
                archive.writestr(info, path.read_bytes())
    return hashlib.sha256(destination.read_bytes()).hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print("Package SHA256:", build(args.output.resolve()))
