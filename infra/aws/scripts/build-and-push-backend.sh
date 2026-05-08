#!/usr/bin/env bash
# Build the production backend image and push to ECR.
# Usage:
#   bash build-and-push-backend.sh <ecr-repo-url> [tag]
# Example:
#   bash build-and-push-backend.sh 1234567890.dkr.ecr.us-east-1.amazonaws.com/ragqa-prod-backend latest
#
# Requirements:
#   - Docker running
#   - aws CLI configured (`aws sts get-caller-identity` works)
#   - You're at the repo root or one of its descendants

set -euo pipefail

ECR_URL="${1:-}"
TAG="${2:-latest}"

if [[ -z "$ECR_URL" ]]; then
  echo "Usage: $0 <ecr-repo-url> [tag]"
  echo "Hint: get the URL from 'terraform output -raw ecr_repository_url'"
  exit 2
fi

REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"

# Find the repo root (two levels up from this script)
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../../.." &> /dev/null && pwd)"

echo "==> repo root  : $REPO_ROOT"
echo "==> ecr url    : $ECR_URL"
echo "==> tag        : $TAG"
echo "==> region     : $REGION"

# Sanity check: backend/data exists (needed because Dockerfile.prod COPY data)
if [[ ! -d "$REPO_ROOT/backend/data" ]]; then
  echo "ERROR: $REPO_ROOT/backend/data does not exist."
  echo "       Run ingestion locally first: python scripts/ingest_pdfs.py"
  exit 3
fi

CHUNK_COUNT=$(ls -1 "$REPO_ROOT/backend/data/" 2>/dev/null | wc -l || echo 0)
echo "==> backend/data has $CHUNK_COUNT entries"

# 1. Authenticate Docker to ECR
echo "==> aws ecr get-login-password | docker login"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "${ECR_URL%/*}"

# 2. Build (using the production Dockerfile)
# --provenance=false --sbom=false: skip the buildx attestation step. On
# Docker Desktop / Windows it has hung repeatedly during the post-build
# "unpack to local store" phase, with the buildx instance becoming
# unresponsive for hours. We don't ship attestations so disabling them
# costs us nothing and avoids the hang.
echo "==> docker build"
docker build \
  --provenance=false \
  --sbom=false \
  -f "$REPO_ROOT/backend/Dockerfile.prod" \
  -t "${ECR_URL}:${TAG}" \
  "$REPO_ROOT/backend"

# 3. Push
echo "==> docker push"
docker push "${ECR_URL}:${TAG}"

echo "==> done. App Runner will auto-deploy when the new image is detected."
echo "    Watch with: aws apprunner list-services --region $REGION"
