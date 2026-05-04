# Copy to terraform.tfvars and edit:
#   cp example.tfvars terraform.tfvars
#
# terraform.tfvars is gitignored.

aws_region        = "us-east-1"
project           = "ragqa"
environment       = "prod"

# CORS - tighten this to your Amplify URL after the first build
# (e.g. "https://main.dXXXXXXXXXX.amplifyapp.com")
frontend_origin   = "*"

# Amplify - leave repository blank to skip auto-deploy and connect manually
github_repository = ""           # e.g. "https://github.com/youruser/rag-qa"
github_branch     = "main"
amplify_oauth_token = ""         # GitHub PAT with repo scope; sensitive
