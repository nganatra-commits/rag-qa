#!/usr/bin/env bash
# Populate the Secrets Manager entries created by terraform.
# Usage:
#   bash set-secrets.sh
# Prompts for the values interactively (so they don't end up in shell history).

set -euo pipefail

PROJECT="${PROJECT:-ragqa}"
ENVIRONMENT="${ENVIRONMENT:-prod}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"

OPENAI_NAME="${PROJECT}-${ENVIRONMENT}/openai-api-key"
PINECONE_NAME="${PROJECT}-${ENVIRONMENT}/pinecone-api-key"

read -r -s -p "OpenAI API key (sk-proj-...): " OPENAI
echo
read -r -s -p "Pinecone API key (pcsk_...):   " PINECONE
echo

echo "==> writing OpenAI key to $OPENAI_NAME"
aws secretsmanager put-secret-value \
  --region "$REGION" \
  --secret-id "$OPENAI_NAME" \
  --secret-string "$OPENAI" \
  > /dev/null

echo "==> writing Pinecone key to $PINECONE_NAME"
aws secretsmanager put-secret-value \
  --region "$REGION" \
  --secret-id "$PINECONE_NAME" \
  --secret-string "$PINECONE" \
  > /dev/null

echo "==> done. Trigger an App Runner re-deploy for the new values to take effect:"
echo "    aws apprunner start-deployment --region $REGION \\"
echo "      --service-arn \$(aws apprunner list-services --region $REGION \\"
echo "        --query 'ServiceSummaryList[?ServiceName==\`${PROJECT}-${ENVIRONMENT}-backend\`].ServiceArn' \\"
echo "        --output text)"
