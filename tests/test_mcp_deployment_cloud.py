import pytest

from backend.foundation_runs import get
from backend.mcp_python_cloud import PythonCloud
from tests.test_mcp_onboarding import setup
from tests.test_mcp_package import archive, reserve, send_part, uploads
from tests.test_mcp_python import CONFIG


def test_retirement_deletes_only_frozen_upload_versions_and_preserves_other_packages(setup, uploads, monkeypatch):
    _, storage, _ = uploads
    data = archive()
    record, _ = reserve(setup, data)
    send_part(setup, record, data)
    setup[0].post("/api/admin/mcp/packages/" + record["id"] + "/deploy", json={})
    with setup[1].tx() as db:
        state = get(db, "mcp-python:" + record["id"])
    import boto3
    cloud = PythonCloud(setup[2].settings, boto3.Session(
        aws_access_key_id="test", aws_secret_access_key="test", region_name="us-west-2"))
    monkeypatch.setattr(cloud, "runtime", lambda *args, **kwargs: None)
    cloud.write("package", state, CONFIG)
    state.update(cloud.read("package", state, CONFIG))
    bucket = CONFIG["artifact"]["bucket"]
    untouched = storage.s3.put_object(Bucket=bucket, Key="mcp/python/another/runtime.zip", Body=b"keep")
    resources = cloud.retirement_plan(state, CONFIG)
    assert len(resources) == 2
    assert {r["key"] for r in resources} == {
        "mcp/python/" + state["id"] + "/runtime.zip", "mcp/python/uploads/" + state["id"] + "/part-0000"}
    assert all(r["version_id"] and r["kind"] == "object" for r in resources)
    for resource in resources:
        assert not cloud.retirement_read(resource, state, CONFIG)["absent"]
        foreign = {**resource, "key": "mcp/python/another/runtime.zip"}
        with pytest.raises(ValueError, match="ownership"):
            cloud.retirement_write(foreign, state, CONFIG)
        cloud.retirement_write(resource, state, CONFIG)
        assert cloud.retirement_read(resource, state, CONFIG)["absent"]
    assert storage.s3.get_object(Bucket=bucket, Key="mcp/python/another/runtime.zip",
        VersionId=untouched["VersionId"])["Body"].read() == b"keep"
    assert not storage.s3.list_object_versions(Bucket=bucket, Prefix="mcp/python/" + state["id"]).get("Versions")


def test_unfinished_upload_can_be_deleted_without_starting_a_runtime(setup, uploads, monkeypatch):
    _, storage, fake = uploads
    record, _ = reserve(setup, archive())
    assert send_part(setup, record, archive()).status_code == 200
    import boto3
    from tests.test_mcp_python import drain
    cloud = PythonCloud(setup[2].settings, boto3.Session(
        aws_access_key_id="test", aws_secret_access_key="test", region_name="us-west-2"))
    monkeypatch.setattr(cloud, "runtime", lambda *args, **kwargs: None)
    for method in ("retirement_plan", "retirement_read", "retirement_write"):
        monkeypatch.setattr(fake, method, getattr(cloud, method), raising=False)
    response = setup[0].post("/api/admin/mcp/deployments/" + record["id"] + "/delete", json={
        "confirm_name": record["name"], "expected_revision": 1, "idempotency_key": "delete-unfinished-0001"})
    assert response.status_code == 202, response.text
    drain(setup, response.json()["job_id"])
    assert not fake.writes
    assert setup[0].get("/api/admin/mcp/packages/" + record["id"]).json()["phase"] == "DELETED"
    assert not storage.s3.list_object_versions(Bucket=CONFIG["artifact"]["bucket"],
        Prefix="mcp/python/uploads/" + record["id"] + "/").get("Versions")
    assert send_part(setup, record, archive()).status_code == 409
    assert setup[0].post("/api/admin/mcp/packages/" + record["id"] + "/deploy", json={}).json()["phase"] == "DELETED"


def test_changed_object_version_or_tags_block_deletion_before_a_write(setup, uploads, monkeypatch):
    _, storage, _ = uploads
    record, _ = reserve(setup, archive())
    send_part(setup, record, archive())
    setup[0].post("/api/admin/mcp/packages/" + record["id"] + "/deploy", json={})
    with setup[1].tx() as db:
        state = get(db, "mcp-python:" + record["id"])
    import boto3
    cloud = PythonCloud(setup[2].settings, boto3.Session(
        aws_access_key_id="test", aws_secret_access_key="test", region_name="us-west-2"))
    monkeypatch.setattr(cloud, "runtime", lambda *args, **kwargs: None)
    resources = cloud.retirement_plan(state, CONFIG)
    resource = resources[0]
    changed = {**resource, "digest": "0" * 64}
    with pytest.raises(ValueError, match="version changed"):
        cloud.retirement_write(changed, state, CONFIG)
    storage.s3.delete_object_tagging(Bucket=resource["bucket"], Key=resource["key"], VersionId=resource["version_id"])
    with pytest.raises(ValueError, match="retention tag"):
        cloud.retirement_write(resource, state, CONFIG)
    assert storage.s3.head_object(Bucket=resource["bucket"], Key=resource["key"],
                                 VersionId=resource["version_id"])["ContentLength"] == resource["size"]


def test_version_pinned_retirement_uses_the_native_s3_delete_contract(setup, uploads, monkeypatch):
    """S3 rejects If-Match with VersionId; the immutable version is the fence."""
    import boto3
    from botocore.exceptions import ClientError

    _, storage, _ = uploads
    record, _ = reserve(setup, archive())
    send_part(setup, record, archive())
    setup[0].post("/api/admin/mcp/packages/" + record["id"] + "/deploy", json={})
    with setup[1].tx() as db:
        state = get(db, "mcp-python:" + record["id"])
    cloud = PythonCloud(setup[2].settings, boto3.Session(
        aws_access_key_id="test", aws_secret_access_key="test", region_name="us-west-2"))
    monkeypatch.setattr(cloud, "runtime", lambda *args, **kwargs: None)
    resource = cloud.retirement_plan(state, CONFIG)[0]
    later = storage.s3.put_object(Bucket=resource["bucket"], Key=resource["key"], Body=b"preserve later version")

    def native_version_contract(params, **kwargs):
        if params.get("VersionId") and "IfMatch" in params:
            raise ClientError({"Error": {"Code": "NotImplemented", "Message":
                "Conditional delete operations are not allowed when a version ID is included in the request parameters."}},
                "DeleteObject")

    cloud.s3.meta.events.register("before-parameter-build.s3.DeleteObject", native_version_contract)
    cloud.retirement_write(resource, state, CONFIG)
    assert cloud.retirement_read(resource, state, CONFIG)["absent"]
    assert storage.s3.get_object(Bucket=resource["bucket"], Key=resource["key"],
        VersionId=later["VersionId"])["Body"].read() == b"preserve later version"
