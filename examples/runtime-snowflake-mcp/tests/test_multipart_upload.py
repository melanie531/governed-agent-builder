import base64
import hashlib
from unittest.mock import Mock

import pytest

from artifact_upload import multipart_upload


def part(data, number):
    return {"PartNumber": number, "ETag": f'"part-{number}"', "Size": len(data),
            "ChecksumSHA256": base64.b64encode(hashlib.sha256(data).digest()).decode()}


def test_large_upload_resumes_confirmed_parts_after_lost_acknowledgement(tmp_path):
    data = b"a" * (5 * 1024 * 1024) + b"b" * (4 * 1024 * 1024)
    package = tmp_path / "runtime.zip"
    package.write_bytes(data)
    first, second = part(data[:5 * 1024 * 1024], 1), part(data[5 * 1024 * 1024:], 2)
    upload = {"bucket": "owned-bucket", "key": "releases/digest/runtime.zip",
              "digest": hashlib.sha256(data).hexdigest()}
    client, save = Mock(), Mock()
    client.create_multipart_upload.return_value = {"UploadId": "owned-upload"}
    client.list_parts.side_effect = [{"Parts": []}, {"Parts": [first, second]}]
    client.upload_part.side_effect = [{"ETag": first["ETag"], "ChecksumSHA256": first["ChecksumSHA256"]}, TimeoutError()]
    client.complete_multipart_upload.return_value = {"VersionId": "immutable-version"}
    with pytest.raises(TimeoutError):
        multipart_upload(client, package, upload, save)
    assert upload["multipart"]["id"] == "owned-upload"
    assert "version" not in upload
    multipart_upload(client, package, upload, save)
    client.create_multipart_upload.assert_called_once()
    assert client.upload_part.call_count == 2
    assert client.complete_multipart_upload.call_args.kwargs["IfNoneMatch"] == "*"
    assert upload["version"] == "immutable-version"


def test_reconciled_part_with_wrong_checksum_is_not_completed(tmp_path):
    package = tmp_path / "runtime.zip"
    package.write_bytes(b"expected")
    upload = {"bucket": "owned-bucket", "key": "key", "digest": "a" * 64, "multipart": {"id": "owned-upload"}}
    client = Mock()
    client.list_parts.return_value = {"Parts": [part(b"other", 1)]}
    with pytest.raises(ValueError, match="checksum"):
        multipart_upload(client, package, upload, Mock())
    client.complete_multipart_upload.assert_not_called()
