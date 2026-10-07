#!/bin/bash
# Build the shared Python layer for Lambda (Python 3.12, linux/x86_64).
# Runs pip inside the AWS SAM build image so compiled wheels (cryptography, cffi)
# match the Lambda runtime and sdist-only packages (http_ece) build on Linux.
# Note: on Apple Silicon the amd64 image runs under emulation, which makes the build slow.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v docker >/dev/null || { echo "docker not found" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker daemon not running" >&2; exit 1; }

# Build in a temp dir at the repo root (NOT under lambda/layer, which CDK zips).
rm -rf .layer-build
mkdir -p .layer-build/python

docker run --rm --platform linux/amd64 \
  -v "$PWD/lambda/layer/requirements.txt":/requirements.txt:ro \
  -v "$PWD/.layer-build/python":/out \
  public.ecr.aws/sam/build-python3.12 \
  pip install --no-cache-dir -r /requirements.txt -t /out

rsync -a --exclude __pycache__ lambda/common/penny_common .layer-build/python/

# Swap in only after everything above succeeded.
rm -rf lambda/layer/python
mv .layer-build/python lambda/layer/python
rm -rf .layer-build
echo "Layer built: $(du -sh lambda/layer/python | cut -f1)"
