"""Accept a reviewed installer-only fix while retaining a partial installation."""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from foundation_harness.config import digest
from scripts.bootstrap_support import json_file
from scripts.studio_install import load_configuration, write_json
from scripts.studio_install_support import InstallTarget, source_manifest


def accept_update(config, *, previous_manifest=None):
    """No deployment writes: verify native ownership, then journal local inputs."""
    manifest_path = Path(previous_manifest) if previous_manifest else config.evidence / "source-manifest.json"
    previous = json_file(manifest_path)
    if not isinstance(previous, dict) or not previous:
        raise ValueError("A retained source manifest is required")
    current = source_manifest(config.root)
    before, after = digest(previous), digest(current)
    changed = sorted(name for name in set(previous) | set(current) if previous.get(name) != current.get(name))
    if any(not name.startswith("scripts/") or not name.endswith(".py") for name in changed):
        raise ValueError("Only installer Python files under scripts/ may change; application inputs must stay pinned")
    saved = json_file(config.state)
    inputs_path = config.evidence / "installation-inputs.json"
    inputs = json_file(inputs_path)
    installation = saved.get("installation", {})
    if (saved.get("target") != config.binding or installation.get("configuration") != config.intent()
            or inputs.get("configuration") != config.intent()):
        raise ValueError("Installation target or configuration differs")
    update = {"previous_source_sha256": before, "source_sha256": after, "changed_files": changed}
    history = installation.get("installer_updates", [])
    recorded = installation.get("source_sha256")
    # The original manifest must match the pinned digest. An interrupted local
    # receipt update can finish only if the exact amendment is already durable.
    if recorded == before:
        if inputs.get("source_sha256") != before:
            raise ValueError("Installation input receipt differs from the retained source")
    elif recorded != after or update not in history or inputs.get("source_sha256") not in (before, after):
        raise ValueError("Source manifest does not match the recorded installation")
    for name, sha in saved.get("installationBuild", {}).get("files", {}).items():
        path = config.evidence / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise RuntimeError("Pinned installation artifact changed: " + name)
    target = InstallTarget(config, recorded)
    if source_manifest(config.root) != current:
        raise RuntimeError("Source changed during installer verification")
    if changed:
        receipt = config.evidence / "installer-updates" / f"{before}-{after}.json"
        write_json(receipt, {**update, "previous_manifest": previous, "current_manifest": current})
        installation = copy.deepcopy(target.state["installation"])
        if installation["source_sha256"] == before:
            installation["source_sha256"] = after
            installation.setdefault("installer_updates", []).append(update)
            target.save("installation", installation)
        write_json(inputs_path, {**inputs, "source_sha256": after})
    write_json(config.evidence / "source-manifest.json", current)
    return {**update, "artifacts_preserved": True, "deployment_writes": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--previous-source-manifest", type=Path,
                        help="For older receipts: a source manifest matching the originally recorded digest")
    parser.add_argument("--worker-memory-size", type=int,
                        help="Recover a verified memory-limit rejection after activation rollback")
    args = parser.parse_args(argv)
    config = load_configuration(args.config)
    if args.worker_memory_size is not None:
        if args.previous_source_manifest:
            parser.error("Accept an installer source update separately before memory recovery")
        from scripts.studio_install_memory import recover_memory
        result = recover_memory(config, args.config, args.worker_memory_size)
    else:
        result = accept_update(config, previous_manifest=args.previous_source_manifest)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
