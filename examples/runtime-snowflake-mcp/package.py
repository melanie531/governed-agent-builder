"""Build a reproducible Linux ARM64 CodeZip from uv.lock, excluding development dependencies."""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent


def build(destination, entry_point=None):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="snowflake-mcp-package-") as temporary:
        work = Path(temporary)
        requirements, target = work / "requirements.txt", work / "package"
        subprocess.run(["uv", "export", "--locked", "--no-dev", "--no-emit-project",
                        "--output-file", str(requirements)], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["uv", "pip", "install", "--target", str(target), "--python-version", "3.13",
                        "--python-platform", "aarch64-manylinux2014", "--only-binary", ":all:",
                        "--require-hashes", "-r", str(requirements)], check=True)
        shutil.copy2(entry_point or ROOT / "main.py", target / "main.py")
        shutil.copy2(ROOT / "mcp-package.json", target / "mcp-package.json")
        shutil.copytree(ROOT / "snowflake_mcp", target / "snowflake_mcp",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(target.rglob("*")):
                relative = path.relative_to(target)
                if (not path.is_file() or "__pycache__" in relative.parts or relative.parts[0] == "bin"
                        or relative.name in {".lock", "INSTALLER", "RECORD", "REQUESTED", "uv_cache.json", "direct_url.json"}):
                    continue
                info = zipfile.ZipInfo(relative.as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())
    return hashlib.sha256(destination.read_bytes()).hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--entry-point", type=Path, help="Optional configured main.py; supporting modules remain included")
    args = parser.parse_args()
    print("CodeZip SHA256:", build(args.output.resolve(), args.entry_point))
