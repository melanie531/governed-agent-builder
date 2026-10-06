import hashlib
from unittest.mock import Mock

from botocore.exceptions import ClientError
import pytest

from deploy import Deployment


def test_uncertain_upload_requires_reconciliation_and_explicit_conditional_retry(tmp_path):
    package = tmp_path / "runtime.zip"
    package.write_bytes(b"locked-package")
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    deployment = Deployment.__new__(Deployment)
    deployment.state = {"upload": {"digest": digest, "bucket": "owned-artifacts", "key": f"releases/{digest}/runtime.zip"}}
    deployment.save = Mock()
    s3 = Mock()
    s3.head_object.side_effect = ClientError(
        {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}}, "HeadObject")
    s3.put_object.return_value = {"VersionId": "immutable-version"}
    deployment.client = lambda name: s3
    with pytest.raises(RuntimeError, match="retry-upload"):
        deployment.upload_package(package, "owned-artifacts", digest, retry_upload=False)
    s3.put_object.assert_not_called()
    result = deployment.upload_package(package, "owned-artifacts", digest, retry_upload=True)
    assert result["version"] == "immutable-version"
    assert s3.head_object.call_count == 2
    assert s3.put_object.call_args.kwargs["IfNoneMatch"] == "*"
    assert s3.put_object.call_args.kwargs["Body"] == package.read_bytes()
    deployment.upload_package(package, "owned-artifacts", digest, retry_upload=False)
    s3.put_object.assert_called_once()
