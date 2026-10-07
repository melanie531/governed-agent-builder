import base64
import hashlib
import io
import json
import stat
import zipfile

import boto3
import pytest
from moto import mock_aws

from backend.foundation_runs import get, put
from tests.test_mcp_onboarding import setup
from tests.test_mcp_python import CONFIG, enable


def archive(entries=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in entries or [
            ("main.py", "from helpers import greeting\n"),
            ("helpers.py", "def greeting():\n    return 'package-owned'\n"),
            ("requirements.txt", "# dependencies are already bundled\n"),
        ]:
            z.writestr(name, content)
    return output.getvalue()


def test_complete_archive_preserves_own_modules_without_an_installed_bundle(tmp_path):
    from backend.mcp_package import validate_archive
    data = archive()
    path = tmp_path / "runtime.zip"
    path.write_bytes(data)
    result = validate_archive(path, hashlib.sha256(data).hexdigest())
    assert result["files"] == 3
    assert path.read_bytes() == data


@pytest.mark.parametrize("entries", [
    [("project/main.py", "pass")],
    [("main.py", "pass"), ("../outside.py", "pass")],
    [("main.py", "pass"), ("/outside.py", "pass")],
    [("main.py", "pass"), ("a\\outside.py", "pass")],
    [("main.py", "pass"), ("C:/outside.py", "pass")],
    [("main.py", "pass"), ("a//b.py", "pass")],
    [("main.py", "pass"), ("a/./b.py", "pass")],
    [("main.py", "pass"), ("main.py", "pass")],
    [("main.py", "def invalid(")],
    [("main.py", "pass"), ("native.so", b"\xcf\xfa\xed\xfe" + b"\0" * 32)],
])
def test_invalid_packages_fail_before_execution(tmp_path, entries):
    from backend.mcp_package import InvalidPackage, validate_archive
    path = tmp_path / "runtime.zip"
    data = archive(entries)
    path.write_bytes(data)
    with pytest.raises(InvalidPackage):
        validate_archive(path, hashlib.sha256(data).hexdigest())


def test_archive_rejects_symlinks_and_expansion_limits(tmp_path, monkeypatch):
    import backend.mcp_package as packages
    path = tmp_path / "runtime.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("main.py", "pass")
        entry = zipfile.ZipInfo("linked.py")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(entry, "main.py")
    with pytest.raises(packages.InvalidPackage):
        packages.validate_archive(path, hashlib.sha256(path.read_bytes()).hexdigest())
    path.write_bytes(archive())
    monkeypatch.setattr(packages, "MAX_EXPANDED", 16)
    with pytest.raises(packages.InvalidPackage):
        packages.validate_archive(path, hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.fixture
def uploads(setup):
    from backend.mcp_package import PackageCloud, PackageUploads
    python, deployment = enable(setup)
    binding = {"bucket": CONFIG["artifact"]["bucket"], "runtime_prefix": CONFIG["runtime_prefix"],
               "gateway_role_arn": "arn:aws:iam::123456789012:role/test-gateway"}
    setup[2].settings["mcp_package_upload"] = binding
    with mock_aws():
        session = boto3.Session(aws_access_key_id="test", aws_secret_access_key="test", region_name="us-west-2")
        cloud = PackageCloud(setup[2].settings, session)
        cloud.s3.create_bucket(Bucket=binding["bucket"], CreateBucketConfiguration={"LocationConstraint": "us-west-2"})
        cloud.s3.put_bucket_versioning(Bucket=binding["bucket"], VersioningConfiguration={"Status": "Enabled"})
        python.packages = PackageUploads(python, cloud)
        yield python.packages, cloud, deployment


def reserve(setup, data, **changes):
    value = {"name": "Complete MCP", "filename": "server.zip", "size": len(data),
             "source_digest": hashlib.sha256(data).hexdigest(), "connection_mode": "IAM",
             "idempotency_key": "complete-package-upload-0001", **changes}
    response = setup[0].post("/api/admin/mcp/packages", json=value)
    assert response.status_code == 201, response.text
    return response.json(), value


def send_part(setup, record, data, index=0, retry=False):
    return setup[0].post(f"/api/admin/mcp/packages/{record['id']}/parts/{index}",
        json={"data": base64.b64encode(data).decode(), "digest": hashlib.sha256(data).hexdigest(), "retry": retry})


def test_complete_package_upload_binds_versions_and_creates_one_job(setup, uploads):
    service, cloud, deployment = uploads
    data = archive()
    record, value = reserve(setup, data)
    assert record["phase"] == "UPLOADING" and record["received_parts"] == []
    assert reserve(setup, data)[0]["id"] == record["id"]
    assert not deployment.writes
    result = send_part(setup, record, data)
    assert result.status_code == 200, result.text
    assert result.json()["received_parts"] == [0]
    key = cloud.part_key(record["id"], 0)
    assert len(cloud.s3.list_object_versions(Bucket=CONFIG["artifact"]["bucket"], Prefix=key)["Versions"]) == 1
    assert send_part(setup, record, data).status_code == 200
    assert len(cloud.s3.list_object_versions(Bucket=CONFIG["artifact"]["bucket"], Prefix=key)["Versions"]) == 1
    path = "/api/admin/mcp/packages/" + record["id"] + "/deploy"
    ready = setup[0].post(path, json={})
    assert ready.status_code == 202, ready.text
    assert setup[0].post(path, json={}).json()["job_id"] == ready.json()["job_id"]
    with setup[1].tx() as db:
        state = get(db, "mcp-python:" + record["id"])
        assert not get(db, "mcp-python-source:" + record["id"])
        assert state["upload_type"] == "package"
        assert state["source_parts"][0]["version_id"]
        assert data.hex() not in json.dumps(state)
        assert "package-owned" not in json.dumps([dict(r) for r in db.select("settings")])
    assert setup[0].get("/api/admin/mcp/package-requests/" + value["idempotency_key"]).json()["job_id"] == ready.json()["job_id"]
    assert setup[0].get("/api/admin/mcp/onboarding-options").json()["python_packages"] is True


def test_part_response_loss_is_reconciled_and_missing_write_needs_explicit_retry(setup, uploads, monkeypatch):
    _, cloud, _ = uploads
    data = archive()
    record, _ = reserve(setup, data)
    original = cloud.write_part
    calls = []
    def lose(*args, **kwargs):
        calls.append(True)
        raise TimeoutError("Do not echo transport details")
    monkeypatch.setattr(cloud, "write_part", lose)
    assert send_part(setup, record, data).status_code == 409
    assert send_part(setup, record, data).status_code == 409
    assert len(calls) == 1
    monkeypatch.setattr(cloud, "write_part", original)
    assert send_part(setup, record, data, retry=True).status_code == 200
    # A response lost after persistence must read the existing version.
    record2, _ = reserve(setup, data, idempotency_key="complete-package-upload-0002")
    def persisted(*args, **kwargs):
        original(*args, **kwargs)
        raise TimeoutError()
    monkeypatch.setattr(cloud, "write_part", persisted)
    assert send_part(setup, record2, data).status_code == 409
    assert setup[0].get("/api/admin/mcp/packages/" + record2["id"]).json()["received_parts"] == [0]
    assert send_part(setup, record2, data).status_code == 200


def test_upload_rejects_changed_contents_unfinished_packages_and_foreign_actor(setup, uploads):
    from tests.conftest import login
    data = archive()
    record, value = reserve(setup, data)
    assert setup[0].post("/api/admin/mcp/packages", json={**value, "size": len(data) + 1}).status_code == 409
    assert send_part(setup, record, data[:-1]).status_code == 422
    assert send_part(setup, record, data, index=1).status_code == 422
    assert setup[0].post("/api/admin/mcp/packages/" + record["id"] + "/deploy", json={}).status_code == 409
    login(setup[0])
    assert send_part(setup, record, data).status_code == 403
    assert setup[0].get("/api/admin/mcp/packages/" + record["id"]).status_code == 403


def test_binary_transfer_has_its_own_bound_without_relaxing_other_request_limits(setup, uploads):
    from backend.mcp_package import PART_BYTES
    data = b"p" * (PART_BYTES + 1)
    record, _ = reserve(setup, data)
    assert send_part(setup, record, data[:PART_BYTES]).status_code == 200
    assert send_part(setup, record, data[PART_BYTES:], index=1).status_code == 200
    assert setup[0].post("/api/admin/mcp/credentials", json={"data": "x" * 70000}).status_code == 413
    assert send_part(setup, record, b"x" * (PART_BYTES + 1024 * 1024)).status_code == 413


@pytest.mark.parametrize("bad_digest", [False, True])
def test_worker_uses_frozen_parts_and_writes_the_exact_uploaded_zip(setup, uploads, bad_digest):
    from backend.mcp_package import InvalidPackage
    from backend.mcp_python_cloud import PythonCloud
    _, cloud, _ = uploads
    data = archive()
    record, _ = reserve(setup, data, **({"source_digest": "0" * 64} if bad_digest else {}))
    assert send_part(setup, record, data).status_code == 200
    assert setup[0].post("/api/admin/mcp/packages/" + record["id"] + "/deploy", json={}).status_code == 202
    with setup[1].tx() as db:
        state = get(db, "mcp-python:" + record["id"])
    native = PythonCloud(setup[2].settings, boto3.Session(
        aws_access_key_id="test", aws_secret_access_key="test", region_name="us-west-2"))
    # No installed source or base bundle is present in S3.
    if bad_digest:
        with pytest.raises(InvalidPackage):
            native.write("package", state, CONFIG)
        assert native.read("package", state, CONFIG) is None
    else:
        native.write("package", state, CONFIG)
        receipt = native.read("package", state, CONFIG)["artifact"]
        assert receipt["digest"] == hashlib.sha256(data).hexdigest()
        assert cloud.s3.get_object(Bucket=receipt["bucket"], Key=receipt["key"], VersionId=receipt["version_id"])["Body"].read() == data
        assert cloud.s3.get_object_tagging(Bucket=receipt["bucket"], Key=receipt["key"])["TagSet"] == [{"Key": "auto-delete", "Value": "no"}]
        request = native.runtime_request({**state, "artifact": receipt}, CONFIG)
        assert "environmentVariables" not in request and "requestHeaderConfiguration" not in request


def test_complete_packages_work_without_any_legacy_snowflake_bundle(setup, uploads):
    from backend.mcp_python_cloud import configuration
    config = {k: v for k, v in CONFIG.items() if k not in ("bundle_name", "artifact", "facade_url")}
    config["package_bucket"] = CONFIG["artifact"]["bucket"]
    assert configuration(config, setup[2].settings) == config
    with setup[1].tx() as db:
        put(db, "mcp-python-config", config)
    data = archive()
    record, _ = reserve(setup, data)
    assert send_part(setup, record, data).status_code == 200
    assert setup[0].post("/api/admin/mcp/packages/" + record["id"] + "/deploy", json={}).status_code == 202
    options = setup[0].get("/api/admin/mcp/onboarding-options").json()
    assert options["python_packages"] is True and options["python_bundle"] is None
    assert "python_packages_reason" not in options


def test_fresh_install_without_package_hosting_reports_an_explicit_reason(setup):
    assert not setup[2].settings.get("mcp_package_upload")
    options = setup[0].get("/api/admin/mcp/onboarding-options").json()
    assert options["python_packages"] is False
    reason = options["python_packages_reason"]
    assert isinstance(reason, str) and reason
    assert "installation" in reason


def test_malformed_package_json_is_rejected_without_echoing_contents(setup, uploads):
    response = setup[0].post("/api/admin/mcp/packages", content=b'{"source":"private-package-content"',
                            headers={"content-type": "application/json"})
    assert response.status_code == 422
    assert "private-package-content" not in response.text


def test_new_package_upload_needs_no_vendor_or_authentication_fields(setup, uploads):
    data = archive()
    payload = {"name": "My own MCP", "filename": "mine.zip", "size": len(data),
               "source_digest": hashlib.sha256(data).hexdigest(),
               "idempotency_key": "generic-package-upload-0001"}
    response = setup[0].post("/api/admin/mcp/packages", json=payload)
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["connection_mode"] == "PACKAGE"
    assert send_part(setup, record, data).status_code == 200
    job = setup[0].post("/api/admin/mcp/packages/" + record["id"] + "/deploy", json={}).json()
    from tests.test_mcp_python import drain
    drain(setup, job["job_id"])
    ready = setup[0].get("/api/admin/mcp/python/" + record["id"]).json()
    assert ready["phase"] == "READY"
    assert "/invocations?qualifier=DEFAULT" in ready["endpoint"]
    with setup[1].tx() as db:
        state = get(db, "mcp-python:" + record["id"])
    assert not {"snowflake_account", "snowflake_role", "warehouse"} & state.keys()


def test_package_declares_its_own_generic_token_validator(tmp_path):
    from backend.mcp_package import validate_archive
    data = archive([("main.py", "pass"), ("mcp-package.json", json.dumps({
        "schema_version": 1, "bearer_validation_tool": "validate_access"}))])
    path = tmp_path / "runtime.zip"
    path.write_bytes(data)
    contract = validate_archive(path, hashlib.sha256(data).hexdigest())
    assert contract["bearer_validation_tool"] == "validate_access"


@pytest.mark.parametrize("manifest", [
    {"schema_version": 2, "bearer_validation_tool": "validate_access"},
    {"schema_version": 1, "bearer_validation_tool": "../private"},
    {"schema_version": 1, "bearer_validation_tool": "validate_access", "secret": "never-accept"},
    {"schema_version": 1, "bearer_validation_tool": ""},
    [],
])
def test_invalid_package_authentication_contract_is_rejected(tmp_path, manifest):
    from backend.mcp_package import InvalidPackage, validate_archive
    data = archive([("main.py", "pass"), ("mcp-package.json", json.dumps(manifest))])
    path = tmp_path / "runtime.zip"
    path.write_bytes(data)
    with pytest.raises(InvalidPackage):
        validate_archive(path, hashlib.sha256(data).hexdigest())


def test_generic_bearer_contract_is_bound_to_the_uploaded_artifact_without_provider_environment(setup, uploads):
    from backend.mcp_python_cloud import ACCESS_TOKEN_HEADER, PythonCloud
    _, storage, _ = uploads
    data = archive([("main.py", "pass"), ("mcp-package.json", json.dumps({
        "schema_version": 1, "bearer_validation_tool": "validate_access"}))])
    record, _ = reserve(setup, data, connection_mode="PACKAGE")
    send_part(setup, record, data)
    setup[0].post("/api/admin/mcp/packages/" + record["id"] + "/deploy", json={})
    with setup[1].tx() as db:
        state = get(db, "mcp-python:" + record["id"])
    cloud = PythonCloud(setup[2].settings, boto3.Session(
        aws_access_key_id="test", aws_secret_access_key="test", region_name="us-west-2"))
    cloud.write("package", state, CONFIG)
    receipt = cloud.read("package", state, CONFIG)
    assert receipt["bearer_validation_tool"] == "validate_access"
    request = cloud.runtime_request({**state, **receipt}, CONFIG)
    assert request["requestHeaderConfiguration"] == {"requestHeaderAllowlist": [ACCESS_TOKEN_HEADER]}
    assert "environmentVariables" not in request
    artifact = receipt["artifact"]
    assert storage.s3.get_object(Bucket=artifact["bucket"], Key=artifact["key"],
                                VersionId=artifact["version_id"])["Body"].read() == data
