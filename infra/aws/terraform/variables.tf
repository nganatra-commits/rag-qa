variable "project" {
  description = "Project tag prefix; used for resource names."
  type        = string
  default     = "ragqa"
}

variable "environment" {
  description = "deployment environment (dev | staging | prod)"
  type        = string
  default     = "prod"
}

variable "aws_region" {
  description = "AWS region for App Runner, ECR, Secrets Manager, Amplify."
  type        = string
  default     = "us-east-1"
}

# --- Backend (App Runner) ---
variable "backend_image_tag" {
  description = "ECR image tag the App Runner service should run."
  type        = string
  default     = "latest"
}

variable "backend_cpu" {
  description = "App Runner CPU. Allowed: 256 | 512 | 1024 | 2048 | 4096"
  type        = string
  default     = "1024"
}

variable "backend_memory" {
  description = "App Runner memory in MB. Allowed: 512 | 1024 | 2048 | 3072 | 4096 ..."
  type        = string
  default     = "2048"
}

variable "backend_min_instances" {
  type    = number
  default = 1
}

variable "backend_max_instances" {
  type    = number
  default = 3
}

# --- Secrets ---
# These are CREATED here (empty) by terraform. After apply, populate them
# via the AWS console or aws CLI:
#   aws secretsmanager put-secret-value --secret-id ragqa/openai-api-key \
#     --secret-string "sk-proj-..."
variable "create_secrets" {
  description = "Create empty Secrets Manager entries for API keys (then you fill them in)."
  type        = bool
  default     = true
}

# --- CORS ---
variable "frontend_origin" {
  description = "The Amplify (or custom-domain) origin allowed to call the backend."
  type        = string
  default     = "*"  # tighten to your Amplify URL after the frontend is up
}

# --- Frontend (Amplify) ---
variable "create_amplify_app" {
  description = "Create an AWS Amplify app for the Next.js frontend."
  type        = bool
  default     = true
}

variable "github_repository" {
  description = "https://github.com/<owner>/<repo> for Amplify auto-deploys. Leave blank to wire manually."
  type        = string
  default     = ""
}

variable "github_branch" {
  type    = string
  default = "main"
}

variable "amplify_oauth_token" {
  description = "GitHub personal access token (sensitive). Create at https://github.com/settings/tokens (repo scope)."
  type        = string
  default     = ""
  sensitive   = true
}
