"""Upload source deltas when the operator's network cannot send large ZIPs.

A temporary, narrowly scoped Lambda copies unchanged compressed entries from a
previous release. The resulting ZIPs must exactly match local SHA-256 digests.
The helper stack is removed after use; it is not part of the deployed product.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.deployment_target import DeploymentTarget, target_arguments

STACK = "governed-agent-builder-journey-upload-helper"


def blocks(data):
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        entries = sorted(source.infolist(), key=lambda item: item.header_offset)
        return {item.filename: data[item.header_offset:entries[index + 1].header_offset if index + 1 < len(entries) else source.start_dir]
                for index, item in enumerate(entries)}, data[source.start_dir:]


def runtime_zip(source):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source)) as original, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for info in original.infolist():
            if info.filename.startswith(("backend/", "scripts/", "tools/", "foundations/")) or info.filename == "uv.lock":
                continue
            archive.writestr(info, original.read(info))
        archive.writestr(zipfile.ZipInfo("main.py", date_time=(2026, 1, 1, 0, 0, 0)),
                         "from runtime.journey.main import create_app\ncreate_app().run()\n")
    return output.getvalue()


def delta_payload(source):
    variants = {"lambda": source, "runtime": runtime_zip(source)}
    metadata, included = {}, set()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as patch:
        for kind, data in variants.items():
            entries, footer = blocks(data)
            metadata[kind] = {"sha256": hashlib.sha256(data).hexdigest(), "entries": []}
            for name, raw in entries.items():
                sha = hashlib.sha256(raw).hexdigest()
                own = name.startswith(("backend/", "foundation_harness/", "runtime/", "scripts/", "tools/", "foundations/")) or name in ("main.py", "uv.lock")
                metadata[kind]["entries"].append({"name": name, "sha256": sha, "patch": own})
                if own and sha not in included:
                    patch.writestr("blocks/" + sha, raw)
                    included.add(sha)
            patch.writestr(kind + ".footer", footer)
        patch.writestr("metadata.json", json.dumps(metadata))
    return output.getvalue(), metadata


ASSEMBLER = '''
import hashlib,io,json,os,zipfile
import boto3

def blocks(data):
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        entries=sorted(source.infolist(),key=lambda x:x.header_offset)
        return {item.filename:data[item.header_offset:entries[i+1].header_offset if i+1<len(entries) else source.start_dir]
                for i,item in enumerate(entries)}

def assemble(base,patch_data):
    base=blocks(base)
    outputs={}
    with zipfile.ZipFile(io.BytesIO(patch_data)) as patch:
        for kind,meta in json.loads(patch.read("metadata.json")).items():
            parts=[]
            for entry in meta["entries"]:
                raw=patch.read("blocks/"+entry["sha256"]) if entry["patch"] else base[entry["name"]]
                if hashlib.sha256(raw).hexdigest()!=entry["sha256"]:
                    raise ValueError("Base ZIP entry differs: "+entry["name"])
                parts.append(raw)
            data=b"".join(parts)+patch.read(kind+".footer")
            if hashlib.sha256(data).hexdigest()!=meta["sha256"]:
                raise ValueError("Assembled ZIP digest mismatch")
            with zipfile.ZipFile(io.BytesIO(data)) as check:
                if check.testzip(): raise ValueError("Invalid assembled ZIP")
            outputs[kind]=(data,meta["sha256"])
    return outputs

def handler(event,context):
    s3=boto3.client("s3");bucket=os.environ["BUCKET"]
    def read(key,limit):
        r=s3.get_object(Bucket=bucket,Key=key)
        data=r["Body"].read(limit+1)
        if len(data)>limit: raise ValueError("Artifact size bound exceeded")
        return data
    patch=read(os.environ["PATCH_KEY"],2000000)
    if hashlib.sha256(patch).hexdigest()!=os.environ["PATCH_SHA"]:
        raise ValueError("Patch digest mismatch")
    outputs=assemble(read(os.environ["BASE_KEY"],40000000),patch)
    result={}
    for kind,(data,sha) in outputs.items():
        key=("releases/"+sha+"/lambda.zip") if kind=="lambda" else ("journey/foundation/"+sha+".zip")
        response=s3.put_object(Bucket=bucket,Key=key,Body=data,ServerSideEncryption="AES256")
        result[kind]={"key":key,"sha256":sha,"version_id":response["VersionId"]}
    return result
'''


def helper_template(bucket, base_key, patch_key, patch_sha, metadata):
    outputs = ["releases/" + metadata["lambda"]["sha256"] + "/lambda.zip",
               "journey/foundation/" + metadata["runtime"]["sha256"] + ".zip"]
    arn = "arn:aws:s3:::" + bucket + "/"
    return {"AWSTemplateFormatVersion": "2010-09-09", "Resources": {
        "Role": {"Type": "AWS::IAM::Role", "Properties": {
            "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{
                "Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]},
            "Policies": [{"PolicyName": "ExactArtifacts", "PolicyDocument": {"Version": "2012-10-17", "Statement": [
                {"Effect": "Allow", "Action": "s3:GetObject", "Resource": [arn + base_key, arn + patch_key]},
                {"Effect": "Allow", "Action": "s3:PutObject", "Resource": [arn + key for key in outputs]}]}}]}},
        "Assembler": {"Type": "AWS::Lambda::Function", "Properties": {
            "Runtime": "python3.13", "Architectures": ["arm64"], "Handler": "index.handler",
            "Role": {"Fn::GetAtt": ["Role", "Arn"]}, "MemorySize": 1024, "Timeout": 300,
            "Code": {"ZipFile": ASSEMBLER},
            "Environment": {"Variables": {"BUCKET": bucket, "BASE_KEY": base_key,
                                          "PATCH_KEY": patch_key, "PATCH_SHA": patch_sha}}}}},
        "Outputs": {"Function": {"Value": {"Ref": "Assembler"}}}}


def wait(cf, stack_id, deleted=False):
    from botocore.exceptions import ClientError
    for _ in range(60):
        try:
            stack = cf.describe_stacks(StackName=stack_id)["Stacks"][0]
        except ClientError as exc:
            if deleted and exc.response["Error"]["Code"] == "ValidationError":
                return None
            raise
        status = stack["StackStatus"]
        if status == ("DELETE_COMPLETE" if deleted else "CREATE_COMPLETE"):
            return stack
        if "FAILED" in status or "ROLLBACK" in status:
            raise RuntimeError("Upload helper stack: " + status)
        time.sleep(10)
    raise TimeoutError("Upload helper stack did not finish")


def main():
    from botocore.config import Config
    parser = argparse.ArgumentParser()
    target_arguments(parser)
    parser.add_argument("--base-key", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"releases/[a-f0-9]{64}/lambda.zip", args.base_key):
        raise ValueError("An exact previous release key is required")
    target = DeploymentTarget(args.expected_account, args.profile, args.region, args.state)
    bucket = target.state["artifacts"]["outputs"]["Bucket"]
    patch, metadata = delta_payload((ROOT / "artifacts/serverless-release.zip").read_bytes())
    if len(patch) > 2000000:
        raise ValueError("Source delta exceeds the upload bound")
    patch_sha = hashlib.sha256(patch).hexdigest()
    patch_key = "journey/upload-patches/" + patch_sha + ".zip"
    s3 = target.session.client("s3", config=Config(connect_timeout=5, read_timeout=30,
        request_checksum_calculation="when_required", s3={"payload_signing_enabled": True}))
    print("Uploading source delta bytes:", len(patch), flush=True)
    uploaded = s3.put_object(Bucket=bucket, Key=patch_key, Body=patch, ServerSideEncryption="AES256")
    stack_id = None
    try:
        result = target.cf.create_stack(StackName=STACK, TemplateBody=json.dumps(
            helper_template(bucket, args.base_key, patch_key, patch_sha, metadata)),
            Capabilities=["CAPABILITY_IAM"], Tags=[{"Key": "project", "Value": "governed-agent-builder"},
                                                {"Key": "purpose", "Value": "temporary-artifact-transfer"}])
        stack_id = result["StackId"]
        print("Assembling exact release ZIPs inside AWS", flush=True)
        stack = wait(target.cf, stack_id)
        function = next(item["OutputValue"] for item in stack["Outputs"] if item["OutputKey"] == "Function")
        response = target.session.client("lambda", config=Config(read_timeout=310, retries={"total_max_attempts": 1})).invoke(
            FunctionName=function, Payload=b"{}")
        result = json.loads(response["Payload"].read())
        if response.get("FunctionError"):
            raise RuntimeError("Artifact assembly failed: " + result.get("errorMessage", "unknown"))
        for kind in metadata:
            if result[kind]["sha256"] != metadata[kind]["sha256"] or result[kind]["version_id"] in (None, "null"):
                raise ValueError("Published artifact differs from the local build")
        target.save("journeyRelease", {
            "lambdaKey": result["lambda"]["key"], "lambdaSha256": result["lambda"]["sha256"],
            "artifact": {"bucket": bucket, **result["runtime"]}})
        print("Both uploaded ZIPs exactly match the local SHA-256 digests", flush=True)
    finally:
        if stack_id:
            target.cf.delete_stack(StackName=stack_id)
            wait(target.cf, stack_id, deleted=True)
        s3.delete_object(Bucket=bucket, Key=patch_key, VersionId=uploaded["VersionId"])
        print("Temporary upload helper and source delta removed", flush=True)


if __name__ == "__main__":
    main()
