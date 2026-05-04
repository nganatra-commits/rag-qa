# Deploying RagQA to AWS

End-to-end production deployment using **AWS App Runner** (backend) +
**AWS Amplify** (frontend) + **Secrets Manager** (API keys) + **ECR**
(container registry). External managed services stay as-is: **Pinecone**
serverless and **OpenAI**.

```
                         ┌────────────────────────┐
                         │  CloudFront (Amplify)  │ ← HTTPS, edge cache
                         │  Next.js SSR + static  │
                         └───────────┬────────────┘
                                     │ HTTPS (BACKEND_URL)
                                     ▼
                         ┌────────────────────────┐
                         │  AWS App Runner        │ ← managed container
                         │  ragqa-prod-backend    │   HTTPS, auto-scale
                         │  Docker image (ECR)    │
                         └───┬────────┬───────────┘
                             │        │
                ┌────────────┘        └────────────┐
                ▼                                  ▼
   ┌──────────────────────┐           ┌──────────────────────┐
   │  Secrets Manager     │           │  Pinecone (managed)  │
   │   openai-api-key     │           │  ragqa-chunks/v1     │
   │   pinecone-api-key   │           │  3072d cosine        │
   └──────────────────────┘           └──────────────────────┘
                                                   │
                                                   ▼
                                       ┌─────────────────────┐
                                       │  OpenAI (managed)   │
                                       │  gpt-4o, embed-3    │
                                       └─────────────────────┘
```

**Cost at low traffic** (~1k queries/month):

| Component | Approx /month |
|---|---:|
| App Runner (1 instance, 1 vCPU/2 GB, idle most of the day) | $25–35 |
| Amplify (basic SSR app, low traffic) | $5–15 |
| ECR (storage for ~5 images @ 800 MB each) | $0.40 |
| Secrets Manager (2 secrets) | $0.80 |
| Data transfer | $1–5 |
| **Total** | **~$35–55** |

Plus the existing Pinecone serverless + OpenAI charges (those don't change).

---

## Prerequisites

- AWS account with admin (or sufficient) IAM permissions
- AWS CLI v2 installed and configured: `aws sts get-caller-identity` works
- Terraform >= 1.6: <https://developer.hashicorp.com/terraform/install>
- Docker Desktop running locally (for the image build)
- Local ingestion already completed (`backend/data/` populated with images,
  cache, `chunks_v1.jsonl`) — we bake it into the container

If you don't have ingestion done locally yet:

```powershell
cd C:\rag-qa\backend
.\.venv\Scripts\Activate.ps1
python scripts/ingest_pdfs.py --wipe-namespace
```

---

## One-time setup

```powershell
cd C:\rag-qa\infra\aws\terraform

# Copy and edit your inputs
copy example.tfvars terraform.tfvars
# (set frontend_origin, optionally github_repository for auto-deploy)

# Initialize providers
terraform init

# Preview
terraform plan -var-file=terraform.tfvars

# Apply (creates ECR, IAM, Secrets Manager, App Runner, optional Amplify)
terraform apply -var-file=terraform.tfvars
```

After `apply`, the `outputs` block prints the next steps. Take note of:

- `ecr_repository_url` — push backend images here
- `backend_service_url` — the App Runner HTTPS endpoint
- `openai_secret_arn`, `pinecone_secret_arn` — fill these in next

---

## Step 1: populate secrets

App Runner won't be healthy until the secrets have values. The terraform
created empty Secrets Manager entries; fill them in:

```bash
# Linux/macOS, or Git Bash on Windows
bash ../scripts/set-secrets.sh
```

It prompts for both keys interactively (so they're not in shell history).
Or do it manually:

```bash
aws secretsmanager put-secret-value \
  --secret-id ragqa-prod/openai-api-key \
  --secret-string "sk-proj-..."

aws secretsmanager put-secret-value \
  --secret-id ragqa-prod/pinecone-api-key \
  --secret-string "pcsk_..."
```

---

## Step 2: build and push the backend image

The production Dockerfile (`backend/Dockerfile.prod`) bakes your already-
ingested `data/` directory into the image. Re-ingestion = rebuild + push.

```bash
ECR_URL=$(terraform output -raw ecr_repository_url)
bash ../scripts/build-and-push-backend.sh "$ECR_URL" latest
```

App Runner has `auto_deployments_enabled = true`, so it picks up the new
:latest tag and rolls out. Watch:

```bash
aws apprunner list-services
aws apprunner describe-service --service-arn <arn>
```

When the service status reaches `RUNNING`:

```bash
curl https://<backend_service_url>/health
# {"status":"ok","version":"0.1.0",...,"indexed_vectors":451}
```

---

## Step 3: deploy the frontend

Two paths.

### A. Amplify auto-deploy from GitHub (recommended)

1. Push your repo to GitHub.
2. Set in `terraform.tfvars`:
   ```
   github_repository   = "https://github.com/youruser/rag-qa"
   github_branch       = "main"
   amplify_oauth_token = "<a GitHub PAT with repo scope>"
   ```
3. `terraform apply` — Amplify connects to the repo and starts the first
   build. The Amplify build spec we created already targets `frontend/` and
   wires `BACKEND_URL` from the App Runner output.
4. Open the Amplify console → your app → **Domain** tab → grab the URL.

### B. Manual upload (if you don't want GitHub integration)

```bash
cd C:\rag-qa\frontend
$env:BACKEND_URL = "https://<backend_service_url>"
npm run build
# upload .next/ via the Amplify console "Deploy without Git" flow
```

---

## Step 4: tighten CORS

Once the frontend is live, set `frontend_origin` in `terraform.tfvars` to
the actual Amplify URL:

```
frontend_origin = "https://main.dXXXXXX.amplifyapp.com"
```

Then `terraform apply` again — App Runner picks up the env var on the next
deploy. The wildcard `*` we started with is fine for testing but allows any
origin to call your backend.

---

## Re-ingesting later

When you add or update PDFs:

```powershell
# Re-run ingestion locally (uses cached captions, only new images cost $)
cd C:\rag-qa\backend
.\.venv\Scripts\Activate.ps1
copy your-new.pdf data\source-pdfs\
python scripts/ingest_pdfs.py --wipe-namespace

# Rebuild and push the image
bash ..\infra\aws\scripts\build-and-push-backend.sh "$ECR_URL" latest
```

App Runner will roll the new image in zero-downtime fashion.

For blue-green: change `RAGQA_PINECONE_NAMESPACE` to `v2`, ingest under the
new namespace, deploy a new App Runner *service* that reads from `v2`,
verify, then flip Amplify's `BACKEND_URL` to the new service.

---

## Tearing down

```bash
terraform destroy -var-file=terraform.tfvars
```

This removes App Runner, Amplify, ECR (with `force_delete=true`), Secrets
Manager (with `recovery_window=0`), and IAM. Pinecone and OpenAI resources
are external and remain. Manually delete the Pinecone index if you want a
full wipe.

---

## What's NOT in this setup (deliberately)

- **VPC / private networking.** App Runner runs in AWS-managed networking.
  If you need to talk to a private VPC (e.g. a private RDS), add an
  `aws_apprunner_vpc_connector` and switch `egress_type = "VPC"`. Adds
  cost (NAT gateway, $32/mo).
- **Custom domain.** Add via Amplify (`aws_amplify_domain_association`)
  and App Runner (`aws_apprunner_custom_domain_association`). Both need
  Route 53 or external DNS.
- **WAF.** Add `aws_wafv2_web_acl` + association if you need rate limiting
  or geo-blocking on the backend.
- **CloudWatch alarms.** `aws_cloudwatch_metric_alarm` on App Runner
  `4XXResponses`, `5XXResponses`, `MemoryUtilization`, etc.

These are 5–20 additional resources each. Add them when you actually need
them; deploying without them first lets you confirm the core works.

---

## Troubleshooting

**App Runner stuck in `OPERATION_IN_PROGRESS` for > 10 min.**
Check the deployment logs:
```bash
aws logs tail /aws/apprunner/ragqa-prod-backend/<service-id>/service --follow
```
Common causes: secret is empty (`insufficient_quota` from OpenAI), image
fails healthcheck (look for `/health` returning non-200).

**`InvalidImageManifest` when App Runner pulls.**
Confirm the image exists: `aws ecr describe-images --repository-name ragqa-prod-backend`.
Make sure you tagged it `:latest` (or whatever `backend_image_tag` is set to).

**Amplify build fails on `npm ci`.**
Add `package-lock.json` to git: `git add frontend/package-lock.json` and
push. Amplify needs the lockfile.

**Backend says `indexed_vectors: 0` but Pinecone has data.**
The Pinecone client is querying the wrong namespace or region. Check the
`RAGQA_PINECONE_*` env vars in the App Runner console match your local
`.env`.
