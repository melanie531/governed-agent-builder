#!/bin/sh
# Host-only dependency resolution/build. This script never deploys or reads AWS credentials.
set -eu
cd "$(dirname "$0")/.."
if [ ! -f runtime/mcp_specialist/requirements-live.lock ]; then
  uv pip compile runtime/mcp_specialist/requirements-live.in --generate-hashes \
    --python-version 3.13 --python-platform aarch64-manylinux_2_28 \
    -o runtime/mcp_specialist/requirements-live.lock
fi
# --require-hashes in Dockerfile.live rejects incomplete or unhashed resolution.
docker buildx build --platform linux/arm64 -f runtime/mcp_specialist/Dockerfile.live \
  --build-arg "IMAGE_BUILD=$(git rev-parse HEAD)" --load -t gab-alpr-live:review .
docker run --rm --entrypoint python gab-alpr-live:review \
  -c 'import boto3, snowflake.connector, mcp; import runtime.mcp_specialist.alpr_live'
