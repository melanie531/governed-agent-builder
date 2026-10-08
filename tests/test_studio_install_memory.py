"""A rejected worker size can be corrected without replacing the installation."""
import copy
import hashlib
import json
from types import SimpleNamespace

import boto3
from moto import mock_aws
import pytest

from backend.dynamo_store import DynamoStore
from backend.foundation_runs import get, put
from scripts.bootstrap_support import PlatformTarget
from scripts.studio_install import load_configuration, write_json
from scripts.studio_install_support import source_digest
from tests.test_studio_install import configuration


@pytest.fixture
def memory_install(tmp_path, monkeypatch):
    from scripts import studio_install_memory as memory
    config = configuration(tmp_path, worker_memory_size=1024)
    path = tmp_path / "install.json"
    source = source_digest(tmp_path)
    platform = {"enabled": True, **config.binding, "worker_memory_size": 1024}
    native = {
        "request_token": "failed-token", "status": "COMPLETE", "result": {"stack_id": "same-app-stack"},
        "request": {"template": {"Resources": {"Worker": {"Type": "AWS::Lambda::Function",
                                                        "Properties": {"MemorySize": 1024}}}},
                    "parameters": {}},
    }
    state = {
        "target": config.binding, "app": {"outputs": {"StateTable": "test-state"}},
        "installation": {"configuration": config.intent(), "source_sha256": source,
                         "steps": {"build": {"status": "COMPLETE"},
                                   "activate": {"status": "NEEDS_RECONCILIATION"}}},
        "journeyPlatform": platform,
        "installationRollbacks": {"activate": {"worker_memory_limit": 512,
                                               "request_token": "failed-token", "stack_id": "same-app-stack"}},
        "platformOperations": {"install-stack-activate": native},
    }
    config.evidence.mkdir(parents=True)
    (config.evidence / "lambda.zip").write_bytes(b"original")
    state["installationBuild"] = {"files": {"lambda.zip": hashlib.sha256(b"original").hexdigest()}}
    write_json(config.state, state)
    write_json(config.evidence / "installation-inputs.json",
               {"configuration": config.intent(), "source_sha256": source})
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name=config.region,
                                  aws_access_key_id="testing", aws_secret_access_key="testing")
        resource.create_table(TableName="test-state", BillingMode="PAY_PER_REQUEST",
                              KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"},
                                         {"AttributeName": "sk", "KeyType": "RANGE"}],
                              AttributeDefinitions=[{"AttributeName": name, "AttributeType": "S"}
                                                    for name in ("pk", "sk")])
        store = DynamoStore("test-state", resource)
        store.initialize()
        with store.tx() as db:
            put(db, "journey-platform", platform)

        def target(selected, expected_source):
            result = PlatformTarget.__new__(PlatformTarget)
            result.path, result.binding = config.state, config.binding
            result.state = json.loads(config.state.read_text())
            assert result.state["installation"]["configuration"] == selected.intent()
            assert result.state["installation"]["source_sha256"] == expected_source
            result.session = SimpleNamespace(resource=lambda _: resource)
            return result

        monkeypatch.setattr(memory, "InstallTarget", target)
        yield memory, config, path, store, native


def test_memory_recovery_updates_bound_settings_and_preserves_failed_intent(memory_install):
    memory, config, path, store, native = memory_install
    result = memory.recover_memory(config, path, 512)
    saved = json.loads(config.state.read_text())
    assert result["worker_memory_size"] == 512
    assert result["cloudformation_submissions"] == 0
    assert saved["platformOperations"]["install-stack-activate"] == native
    assert saved["installation"]["activation_phase"].startswith("activate-memory-512-")
    assert saved["installation"]["steps"]["activate"]["status"] == "NEEDS_RECONCILIATION"
    assert load_configuration(path, root=config.root).worker_memory_size == 512
    with store.tx() as db:
        assert get(db, "journey-platform")["worker_memory_size"] == 512
    original_operations = copy.deepcopy(saved["platformOperations"])
    memory.recover_memory(load_configuration(path, root=config.root), path, 512)
    assert json.loads(config.state.read_text())["platformOperations"] == original_operations


def test_memory_recovery_rejects_value_above_native_limit(memory_install):
    memory, config, path, store, _ = memory_install
    before = config.state.read_bytes()
    with pytest.raises(ValueError, match="limit"):
        memory.recover_memory(config, path, 1024)
    assert config.state.read_bytes() == before
    with store.tx() as db:
        assert get(db, "journey-platform")["worker_memory_size"] == 1024


def test_memory_recovery_does_not_overwrite_changed_platform_settings(memory_install):
    memory, config, path, store, _ = memory_install
    with store.tx() as db:
        platform = get(db, "journey-platform")
        platform["operator_change"] = True
        put(db, "journey-platform", platform)
    with pytest.raises(RuntimeError, match="configuration"):
        memory.recover_memory(config, path, 512)
    with store.tx() as db:
        assert get(db, "journey-platform") == platform


def test_memory_recovery_finishes_interrupted_local_config_publication(memory_install, monkeypatch):
    memory, config, path, store, native = memory_install
    write = memory.write_json
    stopped = False

    def interrupt_once(destination, value):
        nonlocal stopped
        if destination.name == "installation-inputs.json" and not stopped:
            stopped = True
            raise OSError("lost local acknowledgment")
        write(destination, value)

    monkeypatch.setattr(memory, "write_json", interrupt_once)
    with pytest.raises(OSError):
        memory.recover_memory(config, path, 512)
    memory.recover_memory(load_configuration(path, root=config.root), path, 512)
    saved = json.loads(config.state.read_text())
    assert saved["installationMemoryRecovery"]["status"] == "COMPLETE"
    assert saved["platformOperations"]["install-stack-activate"] == native
    assert load_configuration(path, root=config.root).intent() == saved["installation"]["configuration"]
