locals {
  name_prefix = "${var.project}-${var.environment}"
}

# ---------------------------------------------------------------------------
# 1. ECR repository for the backend container image
# ---------------------------------------------------------------------------
resource "aws_ecr_repository" "backend" {
  name                 = "${local.name_prefix}-backend"
  image_tag_mutability = "MUTABLE"
  force_delete         = true   # set to false in real prod

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "backend" {
  repository = aws_ecr_repository.backend.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 10 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

# ---------------------------------------------------------------------------
# 2. Secrets Manager - API keys (create empty; you populate them after apply)
# ---------------------------------------------------------------------------
resource "aws_secretsmanager_secret" "openai_api_key" {
  count       = var.create_secrets ? 1 : 0
  name        = "${local.name_prefix}/openai-api-key"
  description = "OpenAI API key used by ragqa-backend"
  recovery_window_in_days = 0   # allow immediate re-create during dev iter
}

resource "aws_secretsmanager_secret" "pinecone_api_key" {
  count       = var.create_secrets ? 1 : 0
  name        = "${local.name_prefix}/pinecone-api-key"
  description = "Pinecone API key used by ragqa-backend"
  recovery_window_in_days = 0
}

# ---------------------------------------------------------------------------
# 3. IAM roles for App Runner
#    - access role: lets App Runner pull from ECR
#    - instance role: lets the running task read Secrets Manager
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "apprunner_access_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["build.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "apprunner_access" {
  name               = "${local.name_prefix}-apprunner-ecr-access"
  assume_role_policy = data.aws_iam_policy_document.apprunner_access_assume.json
}

resource "aws_iam_role_policy_attachment" "apprunner_access_ecr" {
  role       = aws_iam_role.apprunner_access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

data "aws_iam_policy_document" "apprunner_instance_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["tasks.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "apprunner_instance" {
  name               = "${local.name_prefix}-apprunner-instance"
  assume_role_policy = data.aws_iam_policy_document.apprunner_instance_assume.json
}

data "aws_iam_policy_document" "apprunner_instance_secrets" {
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = compact([
      try(aws_secretsmanager_secret.openai_api_key[0].arn, ""),
      try(aws_secretsmanager_secret.pinecone_api_key[0].arn, ""),
    ])
  }
}

resource "aws_iam_role_policy" "apprunner_instance_secrets" {
  count  = var.create_secrets ? 1 : 0
  name   = "secrets-read"
  role   = aws_iam_role.apprunner_instance.name
  policy = data.aws_iam_policy_document.apprunner_instance_secrets.json
}

# ---------------------------------------------------------------------------
# 4. App Runner service (the backend)
# ---------------------------------------------------------------------------
resource "aws_apprunner_service" "backend" {
  service_name = "${local.name_prefix}-backend"

  source_configuration {
    auto_deployments_enabled = true
    image_repository {
      image_identifier      = "${aws_ecr_repository.backend.repository_url}:${var.backend_image_tag}"
      image_repository_type = "ECR"

      image_configuration {
        port = "8080"

        runtime_environment_variables = {
          RAGQA_HOST           = "0.0.0.0"
          RAGQA_PORT           = "8080"
          RAGQA_DATA_DIR       = "/app/data"
          RAGQA_LOG_LEVEL      = "INFO"
          RAGQA_LOG_JSON       = "true"
          RAGQA_CORS_ORIGINS   = var.frontend_origin
        }

        runtime_environment_secrets = var.create_secrets ? {
          OPENAI_API_KEY   = aws_secretsmanager_secret.openai_api_key[0].arn
          PINECONE_API_KEY = aws_secretsmanager_secret.pinecone_api_key[0].arn
        } : {}
      }
    }

    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_access.arn
    }
  }

  instance_configuration {
    cpu               = var.backend_cpu
    memory            = var.backend_memory
    instance_role_arn = aws_iam_role.apprunner_instance.arn
  }

  health_check_configuration {
    protocol            = "HTTP"
    path                = "/health"
    interval            = 20
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 5
  }

  observability_configuration {
    observability_enabled = false
  }

  depends_on = [
    aws_iam_role_policy_attachment.apprunner_access_ecr,
  ]
}

resource "aws_apprunner_auto_scaling_configuration_version" "default" {
  auto_scaling_configuration_name = "${local.name_prefix}-asc"
  min_size                        = var.backend_min_instances
  max_size                        = var.backend_max_instances
  max_concurrency                 = 50
}

# ---------------------------------------------------------------------------
# 5. Amplify app (frontend) - optional
#    Connect a GitHub repo and Amplify will auto-build on push.
# ---------------------------------------------------------------------------
resource "aws_amplify_app" "frontend" {
  count       = var.create_amplify_app ? 1 : 0
  name        = "${local.name_prefix}-frontend"
  repository  = var.github_repository != "" ? var.github_repository : null
  access_token = var.amplify_oauth_token != "" ? var.amplify_oauth_token : null

  # Built-in framework detection picks Next.js automatically. Override if
  # you have a sub-directory layout: AMPLIFY_MONOREPO_APP_ROOT=frontend.
  environment_variables = {
    AMPLIFY_MONOREPO_APP_ROOT = "frontend"
    BACKEND_URL               = aws_apprunner_service.backend.service_url
    NEXT_PUBLIC_BACKEND_URL   = aws_apprunner_service.backend.service_url
    _LIVE_UPDATES             = "[]"
  }

  build_spec = <<-EOT
    version: 1
    applications:
      - appRoot: frontend
        frontend:
          phases:
            preBuild:
              commands:
                - npm ci
            build:
              commands:
                - npm run build
          artifacts:
            baseDirectory: .next
            files:
              - '**/*'
          cache:
            paths:
              - node_modules/**/*
              - .next/cache/**/*
  EOT
}

resource "aws_amplify_branch" "main" {
  count       = var.create_amplify_app && var.github_repository != "" ? 1 : 0
  app_id      = aws_amplify_app.frontend[0].id
  branch_name = var.github_branch
  framework   = "Next.js - SSR"
  stage       = "PRODUCTION"
  enable_auto_build = true
}
