# ess-work Terraform

AWS 인프라를 Terraform으로 배포합니다. 루트 `installer.py`(boto3)와 **병행 선택** 가능합니다. 같은 계정·같은 `project_name`으로 동시에 배포하지 마세요(이름 충돌).

현재 installer로 배포된 환경 예: [https://d3rip56yv3q50v.cloudfront.net/](https://d3rip56yv3q50v.cloudfront.net/) (`projectName=ess-work`, Cognito + OpenSearch Serverless + LangGraph Runtime).

## 구성

| 모듈 | 리전 | 역할 |
|------|------|------|
| `gateway` | `us-east-1` | AgentCore Web Search Gateway (`gateway-websearch`) |
| `network` | primary | VPC, NAT, VPC Endpoint, SG |
| `data` | primary | S3, OpenSearch Serverless, Bedrock KB |
| `auth` | primary | Cognito, Secrets, CloudFront signing key |
| `storage` | primary | S3 Files (`/mnt/workspace`, `/mnt/app-data`) |
| `edge` | primary | ALB, CloudFront |
| `agentcore` | primary | LangGraph Runtime, Memory, Guardrail, ECR |
| `compute` | primary | ECS Fargate Web UI, ECR |

의존성: `gateway` ‖ `network`/`data`/`auth` → `storage`/`edge` → `agentcore` → `compute`

Provider: **hashicorp/aws >= 6.47.0** (AgentCore Runtime filesystem + S3 Files)

### power-runtime Terraform과의 주요 차이

| 항목 | power-runtime | ess-work |
|------|---------------|----------|
| Auth | 없음 (`user_id` 세션) | **Cognito** User Pool + admin |
| Vector store | S3 Vectors | **OpenSearch Serverless** |
| Gateway | 없음 | AgentCore Web Search (`gateway-websearch`, `us-east-1`) |
| Memory | 없음 | AgentCore Memory |
| Runtime | LangGraph | LangGraph (`runtime_agent/langgraph`) |
| Runtime ECR | `{project}_langgraph` | `{project}_langgraph` |

## Runtime data flow

```text
Browser
  → CloudFront (HTTPS; Sharing URL / 정적 자산·서명 URL)
  → ALB (origin-only HTTP + shared secret header)
  → ECS Fargate Web UI (Cognito 세션, SSE 채팅 API)
  → AgentCore Runtime (LangGraph agent, MCP tools)
       ├─ Amazon Bedrock (모델 추론)
       ├─ Bedrock Knowledge Base → OpenSearch Serverless + S3 docs
       ├─ AgentCore Memory
       ├─ S3 Files mount (/mnt/workspace) ↔ S3 bucket
       └─ Web Search Gateway (us-east-1) via NAT egress
```

## 사전 준비

| 항목 | 설명 |
|------|------|
| Terraform | 1.5+ |
| AWS 자격 증명 | primary 리전 + `us-east-1` Gateway 권한 |
| Docker | `linux/arm64` 빌드 (`docker buildx`) — `skip_docker_build` 시 불필요 |
| Python 3 | vector index Lambda 번들(`pip`) 및 post-deploy 스크립트 |

## 배포

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# cognito_admin_password 수정

terraform init
terraform apply

# Outputs → application/config.json + runtime_agent/langgraph/config.json
python3 scripts/write_config.py

# Observability / Evaluations / Dashboard (installer와 동일, 1회)
python3 scripts/setup_observability.py --refresh-config
```

접속: `terraform output sharing_url` → Cognito `admin` / `cognito_admin_password`.

> **Gateway 주의:** 기본 게이트웨이 이름은 installer와 동일하게 `gateway-websearch`입니다. 계정에 이미 동일 이름 게이트웨이가 있으면(예: agentic-work가 생성) `terraform apply` 전 import하거나, greenfield용으로 `agentcore_websearch_gateway_name`을 프로젝트 고유 값으로 바꾸세요.

## Docker 이미지

기본값: apply 중 `null_resource`가 ECR에 ARM64 이미지를 빌드·푸시합니다.

- Runtime: `runtime_agent/langgraph/Dockerfile` → `{project}_langgraph`
- Web UI: 루트 `Dockerfile` → `ecr-for-{project}`

빌드를 건너뛰려면:

```hcl
skip_docker_build = true
runtime_image_uri = "ACCOUNT.dkr.ecr.REGION.amazonaws.com/ess-work_langgraph:tag"
web_image_uri     = "ACCOUNT.dkr.ecr.REGION.amazonaws.com/ecr-for-ess-work:tag"
```

## 주요 변수

| 변수 | 기본 | 설명 |
|------|------|------|
| `project_name` | `ess-work` | 리소스 이름 prefix |
| `region` | `us-west-2` | primary 리전 |
| `cognito_admin_password` | (필수) | Cognito admin 영구 비밀번호 |
| `agentcore_websearch_gateway_name` | `gateway-websearch` | Web Search Gateway 이름 |
| `skip_docker_build` | `false` | Docker 빌드 스킵 |
| `runtime_image_uri` / `web_image_uri` | `""` | skip 시 필수 |

## 삭제

Observability / Evaluations / CloudWatch Dashboard는 Terraform 상태가 아닙니다.
`setup_observability.py`로 만들었다면 **destroy 전에** cleanup 스크립트를 실행하세요.

```bash
python3 scripts/cleanup_observability.py || true
terraform destroy
```

S3 버킷은 `force_destroy` 설정을 따릅니다. CloudFront 삭제는 수 분이 걸릴 수 있습니다.
AgentCore Runtime 삭제 후 ENI가 subnet/SG를 잠깐 붙잡을 수 있습니다 — ENI가 빠질 때까지 기다린 뒤 `terraform destroy`를 재실행하세요.

## installer와의 차이

- CloudFront 서명키: Terraform `tls_private_key` (installer는 Secrets Manager + 생성 로직)
- OpenSearch 인덱스: Terraform Lambda custom resource (`lambda/create_vector_index`)
- Observability / Evaluations / Dashboard: 스택에 포함하지 않음 — `scripts/setup_observability.py` / `cleanup_observability.py`
