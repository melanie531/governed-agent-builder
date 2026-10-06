"""Bounded S3 parts with checksum reconciliation and an immutable final object."""
import base64
import hashlib
import time

PART_SIZE = 5 * 1024 * 1024


def multipart_upload(client, package, upload, save):
    args = {"Bucket": upload["bucket"], "Key": upload["key"]}
    multipart = upload.get("multipart")
    if multipart is None:
        upload["multipart"] = multipart = {"phase": "CREATING", "started_at": time.time()}
        save()
        result = client.create_multipart_upload(**args, ServerSideEncryption="AES256",
            Metadata={"sha256": upload["digest"]}, Tagging="auto-delete=no", ChecksumAlgorithm="SHA256")
        multipart.update(id=result["UploadId"], phase="UPLOADING")
        save()
    elif not multipart.get("id"):
        raise RuntimeError("Multipart creation acknowledgement is uncertain. Inspect S3 uploads for the retained key before recording its upload ID; no create was repeated.")
    args["UploadId"] = multipart["id"]
    # The example's package is bounded below 50MB. There cannot be >10 parts.
    current = client.list_parts(**args, MaxParts=100)
    if current.get("IsTruncated"):
        raise ValueError("Unexpected multipart inventory")
    existing = {p["PartNumber"]: p for p in current.get("Parts", [])}
    parts = []
    with package.open("rb") as stream:
        number = 0
        while data := stream.read(PART_SIZE):
            number += 1
            checksum = base64.b64encode(hashlib.sha256(data).digest()).decode()
            found = existing.get(number)
            if found:
                if found.get("ChecksumSHA256") != checksum or found["Size"] != len(data):
                    raise ValueError("Retained S3 part checksum or length differs from the locked package")
            else:
                multipart.update(phase="UPLOADING", pending_part=number)
                save()
                found = client.upload_part(**args, PartNumber=number, Body=data, ChecksumSHA256=checksum)
                if found.get("ChecksumSHA256") != checksum:
                    raise ValueError("S3 did not confirm the uploaded part checksum")
            parts.append({"PartNumber": number, "ETag": found["ETag"], "ChecksumSHA256": checksum})
    if set(existing) - {p["PartNumber"] for p in parts}:
        raise ValueError("Unexpected extra S3 multipart parts")
    multipart.update(phase="COMPLETING", parts=parts)
    save()
    result = client.complete_multipart_upload(**args, MultipartUpload={"Parts": parts}, IfNoneMatch="*")
    upload["version"] = result["VersionId"]
    multipart["phase"] = "COMPLETE"
    save()
