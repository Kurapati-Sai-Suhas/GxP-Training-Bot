# AWS Deployment — GxP Training Bot

**Written:** 10 September 2026 · **Verified against AWS documentation the same day.**

> **App Runner is not an option.** AWS closed App Runner to new customers on **30 April 2026**
> and now directs new deployments to **Amazon ECS Express Mode**. Most Django-on-AWS tutorials
> still recommend App Runner; following them will fail at the first command. This guide uses
> ECS Express Mode.

---

## 1. Deployment decision

Four architectures were considered. The decision is recorded with reasons because "why not
SageMaker for everything" is the question this deployment will be asked about.

| Option | Description | Verdict |
|---|---|---|
| **A** | Whole stack on EC2 with Docker Compose | Rejected — no managed scaling, manual TLS, snowflake server |
| **B** | ECS Express Mode + managed data services + NVIDIA NIM for inference | **ADOPTED** |
| **C** | Django on ECS, model inference moved to a SageMaker endpoint | Rejected *for now* — see §7 |
| **D** | Everything inside SageMaker | Rejected — SageMaker hosts models, not Django applications |

**Why B.** The only "model" this system calls is a hosted third-party LLM behind an
OpenAI-compatible HTTP API. There is no trained model artefact to host. Putting a Django
application inside SageMaker would be using an ML serving platform as a generic web host —
more expensive, slower to scale (EC2-backed, minutes not seconds), and with no benefit.

---

## 2. Target architecture

```
                            ┌──────────────────┐
   Browser  ───────────────▶│  CloudFront + S3 │   React SPA (static)
                            └──────────────────┘
                                      │ /api/*
                                      ▼
                          ┌───────────────────────┐
                          │  ECS Express Mode     │  ALB + TLS + autoscaling
                          │  (Fargate)            │  created for you
                          │  ┌─────────────────┐  │
                          │  │ Django/gunicorn │  │  ← /api/health/ready/
                          │  └─────────────────┘  │
                          │  ┌─────────────────┐  │
                          │  │ Celery worker   │  │  separate service, no ALB
                          │  └─────────────────┘  │
                          └───────────────────────┘
                                │     │      │
              ┌─────────────────┘     │      └──────────────────┐
              ▼                       ▼                         ▼
     ┌─────────────────┐   ┌────────────────────┐   ┌────────────────────┐
     │ RDS PostgreSQL  │   │ ElastiCache Valkey │   │ S3 (SOP uploads)   │
     │ 16, private     │   │ broker + results   │   │ private, presigned │
     └─────────────────┘   └────────────────────┘   └────────────────────┘
              │
              ├── Secrets Manager  (SECRET_KEY, DB password, NVIDIA_API_KEY)
              └── CloudWatch Logs  (structured JSON: inference latency, fallback rate)
                                      │
                                      ▼
                          ┌───────────────────────┐
                          │   NVIDIA NIM API      │  openai/gpt-oss-20b
                          │   (external)          │  ← the actual inference layer
                          └───────────────────────┘
```

**No SageMaker in this diagram.** §7 explains that honestly, and what would put it there.

---

## 3. Prerequisites

```bash
aws --version          # need v2
aws sts get-caller-identity
docker --version
```

Set once — every later command reuses these:

```bash
export AWS_REGION=ap-south-1
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export APP=gxp-training-bot
export ECR_URI=$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$APP
```

---

## 4. Build and push the image

```bash
aws ecr create-repository --repository-name $APP --region $AWS_REGION
```

```bash
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com
```

The existing `backend/Dockerfile` is already production-shaped — gunicorn, 3 workers,
`--timeout 180` (deliberate: quiz generation waits synchronously on a Celery result for up
to 120s, and gunicorn's 30s default would kill those requests mid-flight).

```bash
docker build --platform linux/amd64 -t $APP:latest ./backend
```

> `--platform linux/amd64` matters on an Apple Silicon or ARM machine. Fargate runs x86 by
> default and an ARM image fails at task start with an exec-format error.

```bash
docker tag $APP:latest $ECR_URI:latest && docker push $ECR_URI:latest
```

---

## 5. Managed data services

**PostgreSQL:**

```bash
aws rds create-db-instance --db-instance-identifier $APP-db --db-instance-class db.t4g.micro --engine postgres --engine-version 16 --allocated-storage 20 --master-username gxpadmin --manage-master-user-password --no-publicly-accessible --backup-retention-period 7 --region $AWS_REGION
```

`--manage-master-user-password` has AWS generate the password and store it in Secrets
Manager directly. The password never appears in your shell history, your terminal scrollback,
or this document.

**Redis (Valkey):**

```bash
aws elasticache create-serverless-cache --serverless-cache-name $APP-cache --engine valkey --region $AWS_REGION
```

**S3 for uploaded SOPs:**

```bash
aws s3api create-bucket --bucket $APP-media-$ACCOUNT_ID --region $AWS_REGION --create-bucket-configuration LocationConstraint=$AWS_REGION
```

```bash
aws s3api put-public-access-block --bucket $APP-media-$ACCOUNT_ID --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
```

> Uploaded SOPs are controlled documents. The bucket is private with no exception; files are
> served only through the authenticated `GET /api/sops/documents/{id}/download/` endpoint,
> mirroring the decision already recorded in `config/urls.py` about never routing `MEDIA_URL`.

---

## 6. Secrets

Never in the task definition, never in the image, never in git.

```bash
aws secretsmanager create-secret --name $APP/django-secret-key --secret-string "$(python -c 'import secrets;print(secrets.token_urlsafe(64))')" --region $AWS_REGION
```

```bash
aws secretsmanager create-secret --name $APP/nvidia-api-key --secret-string "PASTE_YOUR_KEY_HERE" --region $AWS_REGION
```

Then reference them by ARN in the task definition's `secrets` block, so ECS injects them at
container start:

```json
"secrets": [
  {"name": "SECRET_KEY",     "valueFrom": "arn:aws:secretsmanager:REGION:ACCOUNT:secret:gxp-training-bot/django-secret-key"},
  {"name": "NVIDIA_API_KEY", "valueFrom": "arn:aws:secretsmanager:REGION:ACCOUNT:secret:gxp-training-bot/nvidia-api-key"}
]
```

---

## 7. Deploy the web service

ECS Express Mode creates the Fargate service, ALB, TLS certificate, autoscaling policies and
CloudWatch wiring from one command. It needs two IAM roles: a task **execution** role (to
pull the image and read secrets) and an **infrastructure** role (to create the ALB and
networking).

```bash
aws ecs create-express-gateway-service --primary-container "image=$ECR_URI:latest" --execution-role-arn arn:aws:iam::$ACCOUNT_ID:role/ecsTaskExecutionRole --infrastructure-role-arn arn:aws:iam::$ACCOUNT_ID:role/ecsInfrastructureRole --monitor-resources --region $AWS_REGION
```

Redeploy after a new image push:

```bash
aws ecs update-express-gateway-service --service-arn <SERVICE_ARN> --primary-container '{"image":"'$ECR_URI':latest"}' --region $AWS_REGION
```

> **Verify the flag names** with `aws ecs create-express-gateway-service help` before running.
> Express Mode shipped in November 2025 and gained custom task definitions in July 2026; the
> parameter surface is newer than most cached CLI documentation.

**Health check path:** point it at `/api/health/ready/`, not `/api/health/live/`.
Readiness checks the database and Redis and returns 503 when they are unreachable, so the
load balancer drains the instance. Liveness deliberately checks nothing but the process —
if it queried the database, an RDS failover would restart every container in the service and
turn a recoverable blip into a full outage.

**Celery worker** is a second service from the same image with no load balancer:

```bash
aws ecs create-service --cluster $APP --service-name $APP-worker --task-definition $APP-worker --desired-count 1 --launch-type FARGATE --region $AWS_REGION
```

Its container command overrides to `celery -A config worker --loglevel=info`.

---

## 8. Frontend

```bash
cd frontend && npm ci && VITE_API_BASE_URL=https://<your-service-url> npm run build
```

```bash
aws s3 sync frontend/dist s3://$APP-web-$ACCOUNT_ID --delete
```

Serve through CloudFront with the S3 origin locked to an Origin Access Control, and add
`/api/*` as a second origin pointing at the ALB so the SPA and API share one domain — which
removes the CORS problem rather than configuring around it.

---

## 9. Environment variables

| Variable | Required | Value in production | Notes |
|---|---|---|---|
| `SECRET_KEY` | **Yes** | Secrets Manager | Settings **refuse to boot** with the dev default when `DEBUG=False` |
| `DEBUG` | **Yes** | `False` | |
| `ALLOWED_HOSTS` | **Yes** | your domain(s) | Settings reject `*` when `DEBUG=False` |
| `DATABASE_URL` | **Yes** | `postgres://…` | RDS endpoint |
| `CELERY_BROKER_URL` | **Yes** | `redis://…:6379/0` | ElastiCache endpoint |
| `CELERY_RESULT_BACKEND` | **Yes** | same as broker | |
| `CELERY_TASK_ALWAYS_EAGER` | **Yes** | `False` | `True` would run LLM calls on the request thread |
| `CORS_ALLOWED_ORIGINS` | Yes | frontend origin | Unneeded if CloudFront fronts both |
| `NVIDIA_API_KEY` | No | Secrets Manager | Absent ⇒ permanent offline fallback |
| **`NVIDIA_NIM_MODEL`** | No | `openai/gpt-oss-20b` | **New.** Rotate a retired model without a redeploy |
| **`NVIDIA_EMBED_MODEL`** | No | successor id | **New.** Same reasoning |
| `NVIDIA_NIM_BASE_URL` | No | provider endpoint | **New.** Enables provider switch by config |

The three marked **New** exist because both model ids were previously hardcoded, and both
models were retired by the provider in August 2026 (§11).

---

## 10. Post-deploy verification

```bash
curl -s https://<service-url>/api/health/live/
```

```bash
curl -s https://<service-url>/api/health/ready/ | python -m json.tool
```

Then, authenticated as an admin, confirm the AI path is actually live rather than silently
falling back:

```bash
curl -s -H "Authorization: Token <admin-token>" https://<service-url>/api/health/ai/ | python -m json.tool
```

```bash
curl -s -H "Authorization: Token <admin-token>" https://<service-url>/api/health/metrics/ | python -m json.tool
```

**Read `totals.fallback_rate`.** `0.0` means the live model path is working. `1.0` means
every call is being served by the offline generator — the application is up and the AI is
dead. That distinction is invisible in HTTP status codes, which is the entire reason this
metric exists.

---

## 11. Operational runbook — retired model

The incident this deployment was hardened against, and the fastest interview demo of the
MLOps loop.

**Symptom.** `fallback_rate` climbs to 1.0. No 5xx. No errors. Users still receive questions.

**Detect:**

```bash
aws logs filter-log-events --log-group-name /ecs/$APP --filter-pattern '{ $.error_category = "model_retired" }' --region $AWS_REGION
```

**Confirm:**

```bash
python manage.py check_ai_provider --json
```

Exit codes: `0` healthy · `1` degraded · `2` model retired.

**Find a replacement:**

```bash
python manage.py check_ai_provider --list
```

> Catalogue listing does **not** prove invocability — NIM returns 404 at call time for models
> the account is not entitled to. The command tests real calls.

**Remediate — configuration only, no rebuild, no redeploy:**

```bash
aws secretsmanager put-secret-value --secret-id $APP/nim-model --secret-string "vendor/replacement-model" --region $AWS_REGION
```

```bash
aws ecs update-express-gateway-service --service-arn <SERVICE_ARN> --force-new-deployment --region $AWS_REGION
```

**Verify:** `fallback_rate` returns to 0.0.

**Prevent recurrence** — run the preflight on a schedule and alarm on it:

```bash
aws events put-rule --name $APP-ai-preflight --schedule-expression "rate(1 hour)" --region $AWS_REGION
```

---

## 12. CI/CD

`.github/workflows/ci.yml` already runs migrations and the full suite against real
PostgreSQL with **no** `NVIDIA_API_KEY` — the suite must never depend on a live provider.
Add deployment as a `main`-only job after tests pass:

```yaml
      - name: Build, push and deploy
        run: |
          aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ECR_REGISTRY
          docker build --platform linux/amd64 -t $ECR_URI:$GITHUB_SHA ./backend
          docker push $ECR_URI:$GITHUB_SHA
          aws ecs update-express-gateway-service --service-arn ${{ secrets.SERVICE_ARN }} --primary-container "{\"image\":\"$ECR_URI:$GITHUB_SHA\"}"
```

Authenticate with an OIDC role (`aws-actions/configure-aws-credentials`), not long-lived
access keys in repository secrets.

Tag images with `$GITHUB_SHA`, never only `latest` — rollback is then
`update-express-gateway-service` pointed at the previous SHA.

---

## 13. Cost

| Resource | Configuration | Approx. monthly (ap-south-1) |
|---|---|---|
| ECS Fargate — web | 0.5 vCPU / 1 GB × 1 | ~$15 |
| ECS Fargate — worker | 0.5 vCPU / 1 GB × 1 | ~$15 |
| ALB | shared across Express services | ~$18 |
| RDS PostgreSQL | db.t4g.micro, 20 GB | ~$15 |
| ElastiCache Serverless | minimum | ~$10 |
| S3 + CloudFront | low volume | ~$2 |
| Secrets Manager | 3 secrets | ~$1.20 |
| **Total** | | **~$75–80** |

NVIDIA NIM is billed separately by NVIDIA. **Stop the RDS instance and scale the ECS
services to zero when not demonstrating** — that removes the two largest line items.

---

## 14. Not done, and honestly flagged

| Gap | Impact | Why not done |
|---|---|---|
| **S3 storage backend not wired** | Uploads land on container-local disk and are lost on restart | Needs `django-storages` + `boto3` and a settings change. Deliberately not shipped untested the night before a demo. **Highest-priority follow-up.** |
| No WAF | No L7 rate limiting | Cost, and no public traffic yet |
| Single AZ RDS | No automatic failover | Cost |
| No Prometheus/Grafana | Metrics are per-worker in-process | CloudWatch Logs Insights over the structured logs covers the demo |
| No blue/green | Rolling deploy only | Express Mode default is adequate here |

---

## Sources

- [AWS App Runner](https://aws.amazon.com/apprunner/) — closure to new customers, 30 April 2026
- [Amazon ECS Express Mode](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-overview.html)
- [Build production-ready applications using Amazon ECS Express Mode](https://aws.amazon.com/blogs/aws/build-production-ready-applications-without-infrastructure-complexity-using-amazon-ecs-express-mode) — CLI syntax
- [SageMaker real-time inference](https://docs.aws.amazon.com/sagemaker/latest/dg/realtime-endpoints.html)
