"""Build a pinned AgentCore foundation ZIP with one reviewed source change."""
import argparse
import copy
import hashlib
import os
from pathlib import Path
import tempfile
import zipfile


def replace_member(base, expected_base_sha, member, source, output):
    base, source, output = Path(base), Path(source), Path(output)
    if hashlib.sha256(base.read_bytes()).hexdigest() != expected_base_sha:
        raise ValueError("Base foundation digest differs from the approved release")
    if output.exists():
        raise ValueError("Refusing to overwrite an existing release artifact")
    replacement = source.read_bytes()
    if not replacement:
        raise ValueError("Replacement source is empty")
    with zipfile.ZipFile(base) as original:
        names = original.namelist()
        if len(names) != len(set(names)) or names.count(member) != 1:
            raise ValueError("Selected source is missing or ZIP members are ambiguous")
        if original.read(member) == replacement:
            raise ValueError("Selected source has not changed")
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=output.name + ".", dir=output.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream, zipfile.ZipFile(stream, "w") as patched:
                for info in original.infolist():
                    patched.writestr(copy.copy(info), replacement if info.filename == member else original.read(info))
            with zipfile.ZipFile(temporary) as verified:
                if verified.namelist() != names or verified.testzip():
                    raise ValueError("Patched foundation ZIP failed validation")
                changed = [name for name in names if verified.read(name) != original.read(name)]
                if changed != [member]:
                    raise ValueError("Patched foundation changed an unexpected member")
            os.replace(temporary, output)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return {"base_sha256": expected_base_sha, "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "bytes": output.stat().st_size, "changed_members": changed}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--base-sha256", required=True)
    parser.add_argument("--member", required=True)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(replace_member(args.base, args.base_sha256, args.member, args.source, args.output))
