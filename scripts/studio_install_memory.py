"""Recover a worker-memory rejection after a verified activation rollback."""
import copy
from dataclasses import replace
import hashlib

from backend.dynamo_store import DynamoStore
from backend.foundation_runs import get, put
from foundation_harness.config import digest
from scripts.bootstrap_support import Journal, json_file
from scripts.studio_install import write_json
from scripts.studio_install_support import InstallTarget, source_digest


def recover_memory(config, config_path, memory):
    if type(memory) is not int or not 512 <= memory <= 10240:
        raise ValueError("Worker memory must be between 512 and 10240 MiB")
    saved = json_file(config.state)
    inputs_path = config.evidence / "installation-inputs.json"
    inputs = json_file(inputs_path)
    source = source_digest(config.root)
    if (saved.get("target") != config.binding
            or saved["installation"]["source_sha256"] != source
            or inputs["source_sha256"] != source):
        raise ValueError("Accept the reviewed installer fix before changing worker memory")
    recovery = saved.get("installationMemoryRecovery")
    if recovery:
        request = recovery["request"]
        choices = (request["before_configuration"], request["after_configuration"])
        if (memory != request["after_configuration"]["worker_memory_size"]
                or request["source_sha256"] != source or config.intent() not in choices
                or saved["installation"]["configuration"] not in choices
                or inputs["configuration"] not in choices):
            raise ValueError("Memory recovery configuration differs from its retained intent")
        selected = replace(config, worker_memory_size=saved["installation"]["configuration"]["worker_memory_size"])
    else:
        if (saved["installation"]["configuration"] != config.intent()
                or inputs["configuration"] != config.intent()):
            raise ValueError("Installation configuration changed")
        selected = config
    for name, sha in saved["installationBuild"]["files"].items():
        path = config.evidence / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise RuntimeError("Pinned installation artifact changed: " + name)
    target = InstallTarget(selected, source)
    if not recovery:
        phase = target.state["installation"].get("activation_phase", "activate")
        rollback = target.state.get("installationRollbacks", {}).get(phase)
        if not rollback or not rollback.get("worker_memory_limit") or memory > rollback["worker_memory_limit"]:
            raise ValueError("Worker size must fit the native limit from a verified activation rollback")
        before = target.state["journeyPlatform"]
        if before.get("worker_memory_size") != config.worker_memory_size:
            raise ValueError("Recorded platform memory differs from the installation configuration")
        after = {**before, "worker_memory_size": memory}
        failed = target.state["platformOperations"]["install-stack-" + phase]
        corrected = copy.deepcopy(failed["request"]["template"])
        corrected["Resources"]["Worker"]["Properties"]["MemorySize"] = memory
        request = {
            "before_configuration": config.intent(),
            "after_configuration": replace(config, worker_memory_size=memory).intent(),
            "before_platform": before, "after_platform": after, "source_sha256": source,
            "replaces_phase": phase, "failed_request_token": failed["request_token"],
            "activation_phase": f"activate-memory-{memory}-{digest(rollback)[:12]}",
            "template_digest": digest(corrected), "parameters": failed["request"]["parameters"],
        }
        target.save("installationMemoryRecovery", {"request": request, "status": "PREPARED"})
    if target.state["journeyPlatform"] not in (request["before_platform"], request["after_platform"]):
        raise RuntimeError("Local platform configuration changed during memory recovery")
    store = DynamoStore(target.state["app"]["outputs"]["StateTable"], target.session.resource("dynamodb"))
    result = {"worker_memory_size": memory}

    def read(record=None):
        with store.tx() as db:
            current = get(db, "journey-platform")
        if current not in (request["before_platform"], request["after_platform"]):
            raise RuntimeError("Live platform configuration changed; refusing to overwrite it")
        return result if current == request["after_platform"] else None

    def write(token):
        with store.tx() as db:
            if get(db, "journey-platform") != request["before_platform"]:
                raise RuntimeError("Live platform configuration changed")
            put(db, "journey-platform", request["after_platform"])
        return result

    operation = "install-worker-memory-" + digest(request)[:16]
    if operation not in target.state.get("platformOperations", {}) and read() is not None:
        raise RuntimeError("Unrecorded platform configuration change")
    receipt = Journal(target).run(operation, request, write, read)
    if read() != receipt:
        raise RuntimeError("Worker memory configuration was not verified")
    target.save("journeyPlatform", request["after_platform"])
    installation = copy.deepcopy(target.state["installation"])
    installation.update(configuration=request["after_configuration"],
                        activation_phase=request["activation_phase"])
    target.save("installation", installation)
    write_json(inputs_path, {"configuration": request["after_configuration"], "source_sha256": source})
    write_json(config_path, {**json_file(config_path), "worker_memory_size": memory})
    target.save("installationMemoryRecovery", {"request": request, "status": "COMPLETE"})
    return {**result, "activation_phase": request["activation_phase"],
            "cloudformation_submissions": 0, "next_step": "Run the same deploy command"}
