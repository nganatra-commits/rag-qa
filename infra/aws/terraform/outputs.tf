output "ecr_repository_url" {
  description = "Push backend images here."
  value       = aws_ecr_repository.backend.repository_url
}

output "backend_service_url" {
  description = "App Runner public URL for the backend (HTTPS)."
  value       = "https://${aws_apprunner_service.backend.service_url}"
}

output "openai_secret_arn" {
  description = "Set OPENAI_API_KEY value here after apply."
  value       = try(aws_secretsmanager_secret.openai_api_key[0].arn, null)
}

output "pinecone_secret_arn" {
  description = "Set PINECONE_API_KEY value here after apply."
  value       = try(aws_secretsmanager_secret.pinecone_api_key[0].arn, null)
}

output "amplify_app_id" {
  description = "Amplify app id (use for the Amplify console)."
  value       = try(aws_amplify_app.frontend[0].id, null)
}

output "amplify_default_domain" {
  description = "Amplify default domain (after first build)."
  value       = try(aws_amplify_app.frontend[0].default_domain, null)
}

output "next_steps" {
  value = <<-EOT
    1. Populate the secrets:
       aws secretsmanager put-secret-value \
         --secret-id ${try(aws_secretsmanager_secret.openai_api_key[0].name, "<no secret>")} \
         --secret-string "sk-proj-..."
       aws secretsmanager put-secret-value \
         --secret-id ${try(aws_secretsmanager_secret.pinecone_api_key[0].name, "<no secret>")} \
         --secret-string "pcsk_..."

    2. Build and push the backend image:
       bash ../scripts/build-and-push-backend.sh ${aws_ecr_repository.backend.repository_url}

    3. App Runner will auto-deploy when the image arrives at :latest.
       Watch progress: aws apprunner describe-service --service-arn ${aws_apprunner_service.backend.arn}

    4. Backend URL (HTTPS): https://${aws_apprunner_service.backend.service_url}/health

    5. Frontend (Amplify):
       - Open the Amplify console, find the app "${try(aws_amplify_app.frontend[0].name, local.name_prefix)}-frontend"
       - Connect to your GitHub repo (if not already), then trigger the first build.
       - Build will read BACKEND_URL from env vars and produce a public URL.
  EOT
}
