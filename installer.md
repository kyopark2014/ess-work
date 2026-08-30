# AWS Infrastructure Installer

boto3를 사용하여 AWS 인프라 리소스를 생성하는 Python 스크립트입니다.  
CDK 스택과 동등한 AWS 인프라를 프로그래밍 방식으로 배포합니다.

## 목차

1. [개요](#개요)
2. [설정값](#설정값)
3. [생성되는 리소스](#생성되는-리소스)
4. [주요 함수](#주요-함수)
5. [실행 방법](#실행-방법)
6. [배포 순서](#배포-순서)
7. [AgentCore Runtime installer (별도)](#agentcore-runtime-installer-별도)

---

## 개요

이 스크립트는 **ess-work** 프로젝트의 Web UI(ECS)와 Bedrock Knowledge Base, Cognito 인증, AgentCore Memory 등 ESS Agent용 AWS 인프라를 자동으로 생성합니다.

- **Web UI**: ECS Fargate (`application/` — FastAPI + React) — Cognito 로그인·MCP/Skill 선택·ESS Sync·결과 표시
- **LangGraph Agent**: AgentCore Runtime (`runtime_agent/langgraph/installer.py`) — 추론·MCP·Skill 실행·Guardrail
- MCP 서버는 Runtime 컨테이너 **내부 stdio subprocess**로 기동됩니다. (`runtime_mcp/` 별도 MCP Runtime은 이 저장소에서 사용하지 않습니다.)
- **S3 Files**: AgentCore 세션 checkpoint·ECS `tasks.db`·ESS 세션 데이터 영속화를 위해 S3 버킷을 NFS로 마운트

### 주요 특징
- **완전 자동화**: 단일 스크립트로 ECS·RAG·네트워킹·인증 인프라 배포
- **멱등성**: 이미 존재하는 리소스는 재사용
- **에러 핸들링**: 각 단계별 예외 처리, 실패 시에도 `application/config.json`에 부분 정보 저장
- **로깅**: 상세한 배포 진행 상황 출력
- **OpenSearch Serverless 기반 RAG**: Bedrock Knowledge Base가 S3 Vectors 대신 OpenSearch Serverless collection을 벡터 스토어로 사용
- **Cognito 인증**: Web UI username/password 로그인 + HMAC-signed HttpOnly 세션 쿠키
- **CloudFront Signed Cookies**: S3 정적 경로(`/images`, `/docs`, `/artifacts`, `/session-uploads`) 보호
- **S3 Files 세션 스토리지**: AgentCore `/mnt/workspace` + ECS `/mnt/app-data` 영속 마운트
- **AgentCore Memory**: UserPreference / Summary / Semantic 전략으로 장기 메모리
- **ECS Fargate 배포**: multi-stage Dockerfile 이미지를 ECR에 push한 뒤 ECS Fargate(ARM64) 서비스로 실행
- **AgentCore 연동**: CloudFront URL·S3 Files·Memory 반영 후 LangGraph Runtime installer를 자동 호출
- **SSE 장시간 스트림**: ALB idle timeout 600초, CloudFront origin read timeout 60초
- **AgentCore Web Search Gateway**: `us-east-1` managed web-search connector

### 사전 요구사항
- **ARM64 빌드 호스트**: ECS/AgentCore 이미지는 `linux/arm64` 네이티브 빌드만 지원 (예: t4g, m7g EC2). x86 호스트에서는 QEMU 크로스 빌드 없이 즉시 실패합니다.
- **Python 3.12 + venv**: AgentCore Web Search gateway(`targetConfiguration.mcp.connector`)는 **boto3 >= 1.43.32**가 필요하며, 이 버전은 Python 3.10+에서만 설치됩니다. Amazon Linux 2023 기본 `python3`(3.9)로는 실행하지 마세요.
- **Docker CLI + buildx**: ARM64 호스트에서 컨테이너 이미지 빌드 및 ECR push (`docker buildx build --push`)
- **디스크 여유**: Docker 빌드 전 최소 약 2GB 여유 공간 확인 (`DOCKER_MIN_FREE_MB`)
- **AWS CLI**: ECR 로그인 (`aws ecr get-login-password`)
- **boto3 >= 1.43.32**, **bedrock-agentcore**, **cryptography** 및 스크립트 실행에 필요한 AWS 자격 증명 (S3 Files API용 `s3files` 클라이언트 포함)
- **IAM 권한**: EC2/로컬에서 installer를 실행하는 주체는 S3, IAM, VPC, ECS, ECR, CloudFront, Cognito, Bedrock Agent, OpenSearch Serverless, AgentCore Control/Memory, S3 Vectors(레거시), **S3 Files** (`s3files`) 등 작업 권한이 필요합니다.

---

## 설정값

```python
# 기본 설정 (installer.py 상단)
project_name = "ess-work"            # 프로젝트 이름 (최소 3자)
region = "us-west-2"                 # AWS 리전 (ECS·VPC·KB 등)
AGENTCORE_GATEWAY_REGION = "us-east-1"  # AgentCore Web Search Gateway 전용 리전
AGENTCORE_WEBSEARCH_GATEWAY_NAME = "gateway-websearch"
AGENTCORE_WEBSEARCH_TARGET_NAME = "websearch"
MIN_BOTO3_VERSION_FOR_AGENTCORE_CONNECTOR = "1.43.32"
git_name = "ess-work"

# SSE / ALB 타임아웃
SSE_ORIGIN_READ_TIMEOUT_SECONDS = 60   # CloudFront OriginReadTimeout
ALB_IDLE_TIMEOUT_SECONDS = 600         # 장시간 SSE tool run 대비

# 자동 생성되는 변수
account_id = sts_client.get_caller_identity()["Account"]
bucket_name = f"storage-for-{project_name}-{account_id}-{region}"
vector_bucket_name = f"{project_name}-{account_id}"  # 레거시 S3 Vectors용
vector_index_name = project_name                     # OpenSearch collection 이름

# 벡터 인덱스 설정 (S3 Vectors 레거시 함수용)
embedding_dimensions = 1024
embedding_data_type = "float32"
distance_metric = "cosine"

# S3 Files (AgentCore session storage + ECS app-data)
S3_FILES_SESSION_PREFIX = "agentcore-sessions/"
S3_FILES_APP_DATA_PREFIX = "app-data/"
APP_DATA_MOUNT_PATH = "/mnt/app-data"
SESSION_STORAGE_MOUNT_PATH = "/mnt/workspace"

# AgentCore Runtime 이름: project_name의 '-' → '_' (예: ess_work)
# agent_runtime_name(project_name)

# Cognito
COGNITO_ADMIN_USERNAME = "admin"
COGNITO_CLIENT_NAME = f"{project_name}-web-ui"

# Secrets Manager (소스에 값 하드코딩 없음)
ALB_ORIGIN_HEADER_SECRET_NAME = f"{project_name}/cloudfront-alb-origin-header"
SESSION_SIGNING_KEY_SECRET_NAME = f"{project_name}/session-signing-key"
CLOUDFRONT_SIGNING_KEY_SECRET_NAME = f"{project_name}/cloudfront-signing-key"

# CloudFront S3 signed-cookie 경로
CLOUDFRONT_S3_SIGNED_PATHS = (
    "/images/*",
    "/docs/*",
    "/artifacts/*",
    "/session-uploads/*",
)
```

---

## 생성되는 리소스

### 1. S3 버킷
- **이름**: `storage-for-{project_name}-{account_id}-{region}`
- **설정**:
  - CORS 활성화 (GET, POST, PUT)
  - 퍼블릭 액세스 차단
  - 버전 관리 **Enabled** (S3 Files file system 생성 필수; 신규 bucket은 `create_s3_bucket`에서, 기존 bucket은 S3 Files 생성 시 자동 활성화)
  - `docs/` 폴더 자동 생성
  - S3 Files 세션 prefix: `agentcore-sessions/`
  - ESS 대용량 업로드: `/session-uploads/*` (CloudFront signed cookies)

### 2. IAM 역할 (루트 installer)

| 역할 | 설명 |
|------|------|
| `role-knowledge-base-for-{project_name}-{region}` | Bedrock Knowledge Base용 역할 (OpenSearch Serverless·S3 접근) |
| `role-ecs-task-for-{project_name}-{region}` | ECS 태스크용 역할 (Bedrock, S3, AgentCore invoke, Cognito auth, Secrets Manager, S3 Files mount 등) |
| `role-ecs-execution-for-{project_name}-{region}` | ECS 태스크 실행 역할 (ECR pull, CloudWatch Logs, Secrets Manager) |
| `role-agentcore-memory-for-{project_name}-{region}` | AgentCore Memory용 역할 |
| `role-agentcore-gateway-websearch-for-{project_name}` | AgentCore Web Search gateway용 역할 (`us-east-1`) |
| `role-s3files-sync-for-{project_name}` | S3 Files ↔ S3 bucket 동기화 역할 (`elasticfilesystem.amazonaws.com` trust) |

> `create_lambda_role()`, `create_agent_role()` 함수는 코드에 남아 있으나, 현재 `main()` 배포 흐름에서는 호출되지 않습니다.

**AgentCore Runtime IAM** (`AmazonBedrockAgentCoreRuntimeRoleFor{project_name}`)은 `runtime_agent/langgraph/installer.py`가 별도로 생성·관리합니다. S3 Files 사용 시 `s3files:ClientMount` 등 권한이 조건부로 추가됩니다. ECS Task Role에는 `ensure_ecs_task_s3files_policy()`로 app-data mount 권한이 추가됩니다.

### 2.5. Amazon Cognito (Web UI 인증)

| 리소스 | 설명 |
|--------|------|
| User Pool | 이름 `{project_name}` (`ess-work`) |
| App Client | `{project_name}-web-ui`, `USER_PASSWORD_AUTH` |
| Admin 사용자 | `admin` — installer 시작 시 비밀번호 입력 (기존 admin 있으면 생략) |

세션 쿠키 서명 키는 Secrets Manager `{project_name}/session-signing-key`에 저장됩니다.

### 2.6. AgentCore Memory

| 리소스 | 설명 |
|--------|------|
| Memory | 이름 `{project_name}` (`ess_work` 형태), UserPreference / Summary / Semantic 전략 |
| IAM Role | `role-agentcore-memory-for-{project_name}-{region}` |

`memory_id`, `agentcore_memory_role`이 `application/config.json`에 기록됩니다.

### 3. OpenSearch Serverless (벡터 스토어)

- **Collection 이름**: `{project_name}` (예: `ess-work`)
- **정책**: encryption / network / data access (`enc-{project}`, `net-{project}`, `data-{project}`)
- **엔드포인트**: `collectionEndpoint` — Bedrock Knowledge Base VECTOR 타입 스토어로 연결

> `create_s3_vectors_store()`, `create_knowledge_base_with_s3_vectors()` 함수는 이전 버전 호환을 위해 코드에 남아 있으나, 현재 `main()` 배포 흐름에서는 **OpenSearch Serverless**를 사용합니다.

### 4. VPC 네트워킹

```
VPC (10.20.0.0/16)
├── Public Subnets (2개 AZ)
│   ├── Internet Gateway 연결
│   └── NAT Gateway 호스팅
├── Private Subnets (2개 AZ)
│   └── NAT Gateway + VPC Endpoints (아웃바운드)
├── Security Groups
│   ├── ALB SG (포트 80, CloudFront prefix list)
│   ├── ECS SG (포트 8501, 443)
│   ├── agent-runtime-sg-for-{project_name} (AgentCore microVM)
│   └── s3files-mount-sg-for-{project_name} (NFS 2049)
└── VPC Endpoints
    ├── Interface: bedrock-runtime, ecr.api, ecr.dkr, logs,
    │              secretsmanager, bedrock-agentcore,
    │              bedrock-agentcore-control
    └── Gateway: S3 (ECR 레이어 pull용)
```

Websearch MCP(us-east-1 Gateway)와 web_fetch(npm)는 **NAT Gateway egress**가 필요합니다. 자세한 내용은 [README.md](./README.md)의 네트워크 설정을 참조하세요.

### 4.5. S3 Files (Session + App-data)

VPC 생성 직후 session FS와 app-data FS를 **멱등**으로 프로비저닝합니다.

| 리소스 | 설명 |
|--------|------|
| Sync IAM role | `role-s3files-sync-for-{project_name}` — S3 bucket ↔ NFS 동기화 |
| Session FS | prefix `agentcore-sessions/` — Runtime only (`/mnt/workspace`) |
| App-data FS | prefix `app-data/` — ECS only (`/mnt/app-data`) |
| Mount targets | private subnet마다 FS별 1개 |
| Access points | FS별 마운트 진입점 (`posix uid/gid: 0/0`) |
| Client SGs | runtime SG ↔ session mount SG; ECS SG ↔ app-data mount SG (NFS 2049) |

- **AgentCore**: `/mnt/workspace` → checkpoint / skills / artifacts
- **ECS**: `/mnt/app-data` → `tasks.db` / graph / settings / litellm / ESS 세션 데이터 (`TASK_DB_MOUNT`)
- 마이그레이션: 기존 `agentcore-sessions/`의 application-database·litellm·graph·settings → `app-data/`
- Runtime SG를 Bedrock/AgentCore/Secrets VPC endpoint에 연결

`apply_s3_files_config()`가 `application/config.json`에 session·app-data `s3_files_*` 및 `agent_runtime_vpc_*` 키를 기록합니다.  
Runtime은 session access point ARN이 있으면 **`s3FilesAccessPoint` + VPC 모드**, 없으면 managed **`sessionStorage` + PUBLIC** 으로 생성합니다.

Runtime IAM role 생성 후 `prepare_s3files_for_runtime()`이 session FS 정책을 적용합니다.

### 5. Application Load Balancer
- **타입**: Internet-facing Application Load Balancer
- **리스너**: HTTP 포트 80
- **타겟 그룹**: ECS Fargate 태스크 (IP 타겟, 포트 8501)
- **헬스체크**: `/api/health`
- **Idle timeout**: 600초 (`ALB_IDLE_TIMEOUT_SECONDS`) — 장시간 SSE 스트림 유지
- **Stickiness**: `app_cookie` on `agent_user_id` (86400초). SQLite working-copy 일관성용.  
  `lb_cookie`(AWSALB/AWSALBCORS)는 Secure/HttpOnly를 설정할 수 없어 사용하지 않음.  
  세션 쿠키는 앱이 HttpOnly + Secure(HTTPS) + SameSite=Lax로 발급.
- **Origin 보호**: listener default = **403 fixed-response**, `X-Custom-Header` 일치 시에만 ECS target group으로 forward (`ensure_alb_listener_origin_protection`)

### 6. CloudFront 배포
- **오리진**:
  - 기본: ALB (동적 컨텐츠) — Secrets Manager 오리진 헤더를 Custom Header로 주입
  - `/images/*`, `/docs/*`, `/artifacts/*`, `/session-uploads/*`: S3 (정적 컨텐츠, **signed cookies**)
- **캐시 정책**: Managed-CachingDisabled (ALB), signed-cookie behavior (S3)
- **프로토콜**: HTTP → HTTPS 리다이렉트
- **Origin read timeout**: 60초 (`SSE_ORIGIN_READ_TIMEOUT_SECONDS`)
- **Response Headers Policy**: 프로젝트 custom policy (`{project_name}-security-headers`) — HSTS·CSP·`X-Frame-Options`·origin `Server`/`X-Powered-By` 제거
- **재사용**: 동일 `project_name`의 기존 CloudFront 배포가 있으면 재사용 (헤더·타임아웃·signed-cookie behavior 갱신)

### 6.5. Secrets Manager

| 시크릿 | 용도 |
|--------|------|
| `{project_name}/cloudfront-alb-origin-header` | CloudFront → ALB 오리진 검증용 `X-Custom-Header` 값 |
| `{project_name}/session-signing-key` | Web UI HMAC 세션 쿠키 서명 키 |
| `{project_name}/cloudfront-signing-key` | CloudFront RSA signing key (private PEM, public key id, key group id) |

생성: `get_or_create_alb_origin_header()`, `get_or_create_session_signing_key()`, `get_or_create_cloudfront_signing_material()`  
삭제: `uninstaller.py`의 각 `delete_*_secret()` 함수

### 7. ECR (Elastic Container Registry)
- **리포지토리**: `ecr-for-{project_name}`
- **이미지 태그**: 배포 시각 기반 (`YYYYMMDDHHMMSS`) + ECR에서 `latest`로 promote
- **플랫폼**: `linux/arm64` (AgentCore runtime과 동일; ARM64 EC2에서 네이티브 빌드)
- **빌드 소스**: 프로젝트 루트 multi-stage `Dockerfile` (Node frontend + Python FastAPI + `ess/` + `graph/` deps)
- **빌드 방식**: `docker buildx build --platform linux/arm64 --provenance=false --sbom=false --push`

### 8. ECS Fargate
- **클러스터**: `cluster-for-{project_name}`
- **서비스**: `service-for-{project_name}`
- **태스크 정의**: `task-for-{project_name}`
- **런타임 플랫폼**: `ARM64` / `LINUX` (`runtimePlatform`)
- **컨테이너**: `app` (포트 8501, `uvicorn application.server:app --no-server-header`)
- **CPU / Memory**: 1024 / 2048
- **배포 위치**: Private Subnet (퍼블릭 IP 없음)
- **컨테이너 헬스체크**: `curl -f http://localhost:8501/api/health`
- **볼륨**: S3 Files → `/mnt/app-data` (설정 시)
- **배포 설정**: `minimumHealthyPercent=0`, `maximumPercent=100`, AZ rebalancing DISABLED
- **로그**: CloudWatch Logs `/ecs/app-for-{project_name}`

### 9. Bedrock Knowledge Base
- **스토리지**: OpenSearch Serverless (`OPENSEARCH_SERVERLESS` 타입)
- **임베딩 모델**: Amazon Titan Embed Text v2 (1024차원, FLOAT32)
- **파싱**: 기본 파서 (default parser)
- **청킹**: Fixed Size (300 토큰, 20% 오버랩)
- **데이터 소스**: S3 `docs/` 프리픽스

### 10. AgentCore 리소스

#### AgentCore Web Search Gateway
- **이름**: `gateway-websearch`
- **타겟 이름**: `websearch`
- **리전**: `us-east-1` (AgentCore Gateway 전용)
- **프로토콜**: MCP (`AWS_IAM` 인증)
- **타겟**: managed `web-search` connector
- **용도**: Runtime의 `websearch` MCP (AgentCore Gateway 경유)
- **재사용**: 기존 gateway + websearch target이 있으면 재생성 없이 `application/config.json`에 반영

#### LangGraph Agent Runtime
VPC·S3 Files·Memory 프로비저닝 **후**, CloudFront 배포로 `sharing_url`이 반영된 뒤 루트 installer가 아래 스크립트를 **자동 호출**합니다 (`[11/10]`).

| 런타임 | 설치 스크립트 | ECR / Runtime 이름 |
|--------|--------------|-------------------|
| LangGraph Agent | `runtime_agent/langgraph/installer.py` | `agent_runtime_name(project)` → `{project}_` 형태 (예: `ess_work`) |

Runtime installer가 생성·갱신하는 주요 리소스:
- IAM 정책/역할: `AmazonBedrockAgentCoreRuntimePolicyFor{project_name}`, `AmazonBedrockAgentCoreRuntimeRoleFor{project_name}`
- Bedrock Guardrail: 입력 안전 필터 (`guardrail_id`, `guardrail_name`)
- AgentCore Runtime: `agent_runtime_name(project_name)` (하이픈 → 언더스코어)
- **Session storage (기본)**: S3 Files `s3FilesAccessPoint` @ `/mnt/workspace` + `networkMode: VPC`
- **Fallback**: managed `sessionStorage` + `PUBLIC` (`s3_files_access_point_arn` 없을 때)
- Checkpoint: `chat.py` → `AsyncSqliteSaver` → `/mnt/workspace/langgraph_checkpoints.sqlite`
- Observability / Evaluations / CloudWatch Dashboard
- CloudWatch Logs: `/aws/bedrock-agentcore/runtimes/{runtime_name}-...-DEFAULT`

> OpenAI GPT 5.4/5.5는 Bedrock Mantle Responses API를 사용합니다. Runtime IAM 정책의 `BedrockMantleAccess`에 **모델이 호출하는 Mantle 리전**(예: GPT 5.5 → `us-east-2`)이 포함되어야 합니다. 기본 정책은 Runtime 배포 리전(`config.json`의 `region`)만 허용할 수 있습니다.

---

## 주요 함수

### 인프라 생성 함수

#### `create_s3_bucket()`
S3 버킷 생성, CORS·퍼블릭 액세스 차단, **versioning Enabled** (S3 Files 요구사항)

#### `create_knowledge_base_role()` / `create_ecs_roles()` / `create_agentcore_memory_role()` / `create_agentcore_websearch_gateway_role()`
각 서비스별 IAM 역할 및 인라인 정책 생성

`create_ecs_roles()`는 아래 두 역할을 반환합니다.

```python
{
    "task_role_arn": "...",
    "execution_role_arn": "...",
}
```

ECS Task Role에는 Cognito auth(`cognito-idp:InitiateAuth` 등), Bedrock, AgentCore invoke, S3, Secrets Manager, S3 Files mount 권한이 포함됩니다.

#### `create_cognito_user_pool(admin_password=None)`
Cognito User Pool·App Client·admin 사용자 생성/재사용. admin 미존재 시 `prompt_cognito_admin_password()`로 비밀번호 입력.

#### `create_agentcore_memory(role_arn)`
AgentCore Memory 생성/재사용 (UserPreference + Summary + Semantic)

#### `create_opensearch_collection()` / `create_knowledge_base_with_opensearch()`
OpenSearch Serverless collection·정책 생성 및 Bedrock Knowledge Base 연결

#### `create_vpc()` / `create_alb()` / `create_cloudfront_distribution()`
VPC·ALB·CloudFront 생성

- VPC: Bedrock Runtime + `ensure_private_subnet_vpc_endpoints()` (ECR, Logs, Secrets, AgentCore, S3 gateway)
- ALB: `ensure_alb_idle_timeout()` (600초), SG는 CloudFront prefix list만 허용
- CloudFront: ALB 오리진에 `X-Custom-Header` 주입, origin read timeout 60초, S3 signed-cookie behaviors, custom ResponseHeadersPolicy

#### `get_or_create_alb_origin_header()` / `get_or_create_session_signing_key()` / `get_or_create_cloudfront_signing_material()`
Secrets Manager에 오리진 헤더·세션 서명 키·CloudFront RSA signing material 생성·재사용

#### `ensure_alb_listener_origin_protection()`
ALB listener를 default 403 + 헤더 일치 시 forward로 맞춤

#### `create_s3_files_session_storage(vpc_info, s3_bucket_name)`
Runtime 전용 S3 Files (`agentcore-sessions/` → `/mnt/workspace`). ECS는 마운트하지 않음.

#### `create_s3_files_app_data_storage(vpc_info, s3_bucket_name, ...)`
ECS 전용 S3 Files (`app-data/` → `/mnt/app-data`). 마이그레이션 + `prepare_s3files_for_ecs()`.

#### `prepare_s3files_for_runtime()` / `prepare_s3files_for_ecs()`
Runtime/ECS IAM role에 S3 Files file system policy 적용

#### `apply_s3_files_config(app_config, s3_files_info, s3_files_app_data_info=None)`
Session·app-data S3 Files·VPC 키를 `application/config.json` 페이로드에 병합.

#### `create_ecr_repository()` / `build_and_push_docker_image()`
ECR 리포지토리 생성, ARM64 Docker buildx 빌드·push

```python
def build_and_push_docker_image(repository_uri, image_tag=None) -> Tuple[str, str]:
    # _require_arm64_build_host() — ARM64 EC2(t4g, m7g) 필수
    # _ensure_docker_disk_space() — 최소 2048MB 여유 확인
    # image_tag 미지정 시 generate_image_build_tag() → YYYYMMDDHHMMSS
    # docker buildx build --platform linux/arm64 --push
    # _promote_ecr_image_tag(..., "latest")
    return image_uri, image_tag
```

#### `deploy_ecs_service(..., s3_files_info=None)`
ECS Fargate 서비스 배포 (태스크 정의, ALB 연동, S3 Files 볼륨 `/mnt/app-data` 포함)

#### `get_or_create_agentcore_websearch_gateway()`
AgentCore Web Search gateway 및 managed web-search 타겟 생성/조회

#### `ensure_boto3_supports_agentcore_connector()`
boto3/botocore >= 1.43.32 검증 (미만이면 pip upgrade 안내 후 종료)

#### `sync_application_capability_lists()`
`runtime_agent/langgraph/mcp.list`, `skills.list`를 `application/`으로 복사 (컨테이너 빌드 전)

#### `build_app_environment()` / `write_application_config()` / `_merge_runtime_agent_settings()`
컨테이너·로컬 개발용 `application/config.json` 생성. Cognito·Memory·OpenSearch·S3 Files 키 병합. Runtime installer가 기록한 `agent_runtime_arn` 등을 `_merge_runtime_agent_settings()`로 병합합니다.

#### `install_agent_runtime(runtime_type="langgraph")`
`runtime_agent/langgraph/installer.py`를 subprocess로 실행하여 AgentCore Runtime 배포 (S3 Files + VPC 모드 반영). Runtime 이름은 `agent_runtime_name()`으로 로그에 표시됩니다.

### 헬퍼 함수

| 함수 | 설명 |
|------|------|
| `agent_runtime_name()` | `project_name`의 `-` → `_` (AgentCore Runtime 이름) |
| `attach_inline_policy()` | IAM 역할에 인라인 정책 연결 |
| `ensure_data_source()` | Knowledge Base S3 데이터 소스 생성/조회 |
| `delete_knowledge_base()` | Knowledge Base 및 데이터 소스 삭제 |
| `create_security_group()` / `create_vpc_endpoint()` | 보안 그룹·Interface VPC 엔드포인트 생성 |
| `create_s3_gateway_vpc_endpoint()` / `ensure_private_subnet_vpc_endpoints()` | S3 Gateway + ECR/Logs/Secrets/AgentCore 엔드포인트 |
| `ensure_alb_idle_timeout()` | ALB idle timeout 600초 |
| `get_or_create_alb_origin_header()` | Secrets Manager 오리진 헤더 생성·재사용 |
| `ensure_alb_listener_origin_protection()` | ALB default 403 + 커스텀 헤더 forward |
| `get_or_create_cloudfront_signing_material()` / `ensure_cloudfront_s3_signed_cookies()` | CloudFront signed cookies 설정 |
| `get_or_create_cloudfront_response_headers_policy()` / `ensure_cloudfront_security_headers()` | 보안 응답 헤더 policy |
| `ensure_cloudfront_oai_bucket_policy()` | OAI GetObject를 CF-served prefix로 제한 |
| `_ensure_s3_bucket_versioning_enabled()` | S3 bucket versioning Enabled (S3 Files 필수) |
| `_get_or_create_s3files_sync_role()` | S3 Files sync IAM role |
| `_get_or_create_s3files_file_system()` | S3 Files FS (prefix별: `agentcore-sessions/` 또는 `app-data/`) |
| `_ensure_s3files_mount_targets()` | private subnet별 mount target |
| `_get_or_create_s3files_access_point()` | S3 Files access point |
| `ensure_ecs_task_s3files_policy()` | ECS task role에 **app-data** S3 Files mount 권한 |
| `_ensure_agent_runtime_vpc_endpoint_access()` | Runtime SG를 VPC endpoint에 연결 |
| `_wait_for_s3files_status()` | S3 Files 리소스 available 폴링 |
| `create_public_subnets()` / `create_private_subnets()` | 서브넷 생성 |
| `get_or_create_internet_gateway()` / `get_or_create_nat_gateway()` | IGW/NAT Gateway 조회/생성 |
| `ensure_private_subnet_nat_routing()` | Private subnet NAT 기본 라우트 보장 |
| `classify_subnets()` | 서브넷을 퍼블릭/프라이빗으로 분류 |
| `wait_for_subnet_available()` / `wait_for_nat_gateway()` | 리소스 가용 상태 대기 |
| `create_ecs_log_group()` / `create_ecs_cluster()` / `ensure_ecs_service_linked_role()` | ECS 로그·클러스터·SLR |
| `create_alb_target_group_for_ecs()` | Fargate용 IP 타겟 그룹 + stickiness |
| `create_alb_listener_with_target_group()` | ALB 리스너·오리진 헤더 보호 규칙 |
| `_wait_for_ecs_service_ready()` | ECS 서비스/타겟 안정화 대기 |
| `_require_arm64_build_host()` | ARM64 EC2에서만 Docker 빌드 허용 |
| `_ensure_native_buildx_builder()` / `_ensure_docker_disk_space()` | buildx·디스크 공간 준비 |
| `_promote_ecr_image_tag()` | 빌드 태그를 `latest`로 promote |
| `generate_image_build_tag()` / `resolve_ecr_image_uri()` | 이미지 태그 생성·ECR URI 조회 |
| `check_application_ready()` | CloudFront URL 애플리케이션 준비 상태 확인 |
| `build_config_from_deployment_state()` | 부분 배포 실패 시 config.json 복구용 payload 생성 |
| `prompt_cognito_admin_password()` | Cognito admin 비밀번호 대화형 입력 |

### 레거시 함수 (main()에서 미사용)

| 함수 | 설명 |
|------|------|
| `create_s3_vectors_store()` / `create_knowledge_base_with_s3_vectors()` | S3 Vectors (레거시) |
| `create_lambda_role()` | Lambda RAG 역할 (레거시) |
| `get_setup_script()` | EC2 User Data / SSM 설정 스크립트 생성 |
| `run_setup_script_via_ssm()` | SSM Run Command로 설정 스크립트 실행 |
| `create_ec2_instance()` | EC2 인스턴스 생성 |
| `create_alb_target_group_and_listener()` | EC2 instance 타겟 그룹 등록 |
| `verify_ec2_subnet_deployment()` | EC2 서브넷 배포 검증 |

---

## 실행 방법

### 사전 준비 (ARM64 EC2)

```bash
# Python 3.12 venv (Amazon Linux 2023)
sudo dnf install -y git python3.12 python3.12-pip docker
sudo systemctl start docker && sudo usermod -aG docker ec2-user

git clone https://github.com/kyopark2014/ess-work
cd ess-work

python3.12 -m venv .venv
source .venv/bin/activate
pip install boto3 bedrock-agentcore cryptography

# boto3 >= 1.43.32 확인
python -c "import boto3, botocore; print(boto3.__version__, botocore.__version__)"
```

### 기본 실행 (전체 인프라 배포)

```bash
python installer.py
```

Cognito admin 사용자가 없으면 시작 시 **admin 비밀번호**를 입력합니다.  
ARM64 EC2에서 Docker buildx로 `linux/arm64` Web UI 이미지를 빌드·push하고, LangGraph Agent Runtime을 설치한 뒤 ECS Fargate(ARM64) 서비스를 생성합니다.

배포 중 `application/config.json`이 먼저 쓰이면 로컬 테스트가 가능합니다:

```bash
uvicorn application.server:app --host 0.0.0.0 --port 8501
```

배포 완료 후 CloudFront URL에서 `admin` / 설치 시 설정한 비밀번호로 로그인합니다.

### Docker 빌드 생략 (기존 ECR 이미지 재사용)

```bash
python installer.py --skip-docker-build
```

`application/config.json`의 `latest_image_tag`/`build_number`, 또는 ECR의 최신 태그, 없으면 `:latest`를 사용합니다. 인프라만 재배포하거나 태스크 정의만 갱신할 때 유용합니다.

### Agent Runtime만 재설치

```bash
python installer.py --install-agent-runtime
python installer.py --install-agent-runtime langgraph
```

루트 인프라 배포 없이 `runtime_agent/langgraph/installer.py`만 실행합니다. Runtime 이미지 재빌드·IAM·Guardrail·AgentCore Runtime 업데이트가 필요할 때 사용합니다. 성공 시 `prepare_s3files_for_runtime()`으로 session S3 Files 정책을 적용합니다.

### 레거시: 기존 EC2 인스턴스에 설정 스크립트 실행

```bash
python installer.py --run-setup
python installer.py --run-setup i-1234567890abcdef0
```

> 현재 기본 배포는 ECS Fargate입니다. `--run-setup`은 이전 EC2 배포 환경 호환용입니다.

### 레거시: EC2 서브넷 배포 검증

```bash
python installer.py --verify-deployment
```

---

## 배포 순서

스크립트는 다음 순서로 리소스를 생성합니다:

```
[시작] Cognito admin 비밀번호 입력 (admin 미존재 시)
       ↓
[1/10] S3 버킷 생성 (versioning Enabled)
       ↓
[2/10] IAM 역할 생성
       • Knowledge Base 역할
       • ECS Task / Execution 역할
       • AgentCore Memory 역할
       • AgentCore Memory 생성
       • AgentCore Web Search gateway 역할 (없을 때만)
       • AgentCore Web Search gateway 생성/재사용 (us-east-1)
       ↓
[2.5/10] Cognito User Pool + App Client + admin 사용자
       ↓
[4/10] OpenSearch Serverless collection 생성
       • encryption / network / data access 정책
       ↓
[4.5/10] Bedrock Knowledge Base 생성
       • OpenSearch Serverless 연결
       • S3 데이터 소스 (docs/) 연결
       ↓
[5/10] VPC 네트워킹 리소스 생성
       • VPC, 서브넷, IGW, NAT Gateway
       • 보안 그룹 (ALB SG, ECS SG)
       • VPC 엔드포인트 (Bedrock, ECR, Logs, Secrets, AgentCore, S3)
       ↓
[5.5/10] S3 Files 세션 스토리지 생성 (Runtime)
       • sync role, file system, mount targets, access point
       • agent-runtime-sg / s3files-mount-sg (NFS 2049)
       ↓
[5.6/10] S3 Files app-data 스토리지 생성 (ECS)
       • app-data/ prefix, ECS task role S3 Files 정책
       ↓
[5.7/10] ALB origin header secret (Secrets Manager)
       ↓
[6/10] Application Load Balancer 생성
       • idle timeout 600초
       ↓
[7/10] CloudFront 배포 생성 (또는 기존 배포 재사용)
       • OAI 생성, S3 bucket policy (prefix 제한)
       • ALB + S3 하이브리드 오리진
       • Signed cookies (/images, /docs, /artifacts, /session-uploads)
       • Custom ResponseHeadersPolicy
       • Origin read timeout 60초
       ↓
[8/10] 앱 설정·Runtime·컨테이너 이미지
       • mcp.list / skills.list → application/ 동기화
       • application/config.json 생성 (Cognito, Memory, OpenSearch, S3 Files, sharing_url)
       • [11/10] runtime_agent/langgraph/installer.py 실행
         (Guardrail + s3FilesAccessPoint + VPC 또는 sessionStorage fallback)
       • prepare_s3files_for_runtime() — Runtime IAM에 session FS policy
       • ECS task AgentCore IAM policy refresh
       • ECR 리포지토리 생성
       • Dockerfile 기반 linux/arm64 buildx 빌드 및 push (태그 + latest)
       ↓
[9/10] ECS Fargate 서비스 배포
       • CloudWatch Logs 그룹 생성
       • IP 타겟 그룹 + stickiness + ALB 리스너
       • 태스크 정의 등록 (S3 Files /mnt/app-data 마운트, APP_CONFIG_JSON)
       • Private Subnet에 Fargate ARM64 태스크 실행
       ↓
[10/10] 애플리케이션 준비 상태 확인 (CloudFront URL)
       ↓
완료 - application/config.json 업데이트 (finally 블록)
```

---

## AgentCore Runtime installer (별도)

LangGraph Agent는 루트 installer의 [8/10] 단계에서 자동 호출되거나, `--install-agent-runtime`으로 단독 실행할 수 있습니다.

```bash
cd runtime_agent/langgraph
python installer.py
```

| 단계 | 함수 | 설명 |
|------|------|------|
| 1 | `update_knowledge_base_config()` | 루트 `project_name`으로 KB ID 조회 → `config.json` 반영 |
| 2 | `create_iam_policies()` | Runtime IAM 정책·역할 (Bedrock AgentCore, Mantle, ECR, Logs, S3 Files 등) |
| 3 | `create_bedrock_guardrail()` | Bedrock Guardrail 생성/업데이트 |
| 4 | `push_to_ecr()` | Runtime Dockerfile `linux/arm64` 빌드 → ECR push |
| 5 | `create_agent_runtime()` | AgentCore Runtime 생성/업데이트 (`s3FilesAccessPoint` + VPC 또는 `sessionStorage` fallback) |
| 6 | `setup_agentcore_observability()` | AgentCore Observability 설정 |
| 7 | `setup_agentcore_evaluations()` | AgentCore Evaluations 설정 |
| 8 | `create_monitoring_dashboard()` | CloudWatch 모니터링 대시보드 생성 |

Runtime installer 내부:
- `session_storage_filesystem_configurations(config)` — S3 Files / managed 분기
- `agent_runtime_network_configuration(config)` — VPC / PUBLIC 분기
- `load_config()` → `_merge_application_config()` — `application/config.json`의 S3 Files·Memory 키 동기화
- Runtime 이름: `agent_runtime_name(projectName)` (예: `ess_work`)

완료 후 `runtime_agent/langgraph/config.json`에 `agent_runtime_arn`, `agent_runtime_role`, `guardrail_id` 등이 기록되며, 루트 installer가 이를 `application/config.json`에 병합합니다.

---

## 배포 완료 후

배포가 완료되면 다음 정보가 출력됩니다:

```
================================================================
Infrastructure Deployment Completed Successfully!
================================================================
Summary:
  S3 Bucket: storage-for-ess-work-{account_id}-us-west-2
  VPC ID: vpc-xxxxxxxxx
  Public Subnets: subnet-xxx, subnet-yyy
  Private Subnets: subnet-aaa, subnet-bbb
  ALB DNS: http://alb-for-ess-work-xxxxxx.us-west-2.elb.amazonaws.com/
  CloudFront Domain: https://xxxxxxxxx.cloudfront.net
  ECS Service: service-for-ess-work (Fargate in private subnet)
  ECR Image: {account_id}.dkr.ecr.us-west-2.amazonaws.com/ecr-for-ess-work:YYYYMMDDHHMMSS
  Build Number: YYYYMMDDHHMMSS
  OpenSearch Endpoint: https://....
  OpenSearch Collection ARN: arn:aws:aoss:...
  Knowledge Base ID: XXXXXXXXXX
  Knowledge Base Role: arn:aws:iam::...
  Cognito User Pool: us-west-2_xxxxx (ess-work)
  Cognito Client ID: xxxxxxxxx
  Cognito Admin: admin
  AgentCore Memory Role: arn:aws:iam::...
  AgentCore Memory ID: mem-xxxxxxxx
  AgentCore Web Search Gateway: gateway-websearch (gateway-xxxxxxxx)
  AgentCore Web Search Gateway URL: https://...
  S3 Files (Runtime) AP: arn:aws:s3files:... (prefix=agentcore-sessions/)
  Agent Runtime Subnets: subnet-aaa, subnet-bbb
  S3 Files (ECS app-data) AP: arn:aws:s3files:...
  S3 Files (ECS app-data) Mount: /mnt/app-data (prefix=app-data/)

Total deployment time: XX.XX minutes
================================================================
```

로그인: CloudFront URL → username `admin` / 설치 시 설정한 비밀번호

### application/config.json

배포 성공/실패와 관계없이 `finally` 블록에서 `application/config.json`이 갱신됩니다. 주요 필드:

| 필드 | 설명 |
|------|------|
| `projectName`, `accountId`, `region` | 프로젝트 기본 정보 |
| `knowledge_base_id`, `data_source_id` | Bedrock Knowledge Base |
| `knowledge_base_role` | Knowledge Base IAM 역할 ARN |
| `collectionArn`, `opensearch_url` | OpenSearch Serverless collection |
| `vector_bucket_name`, `vector_bucket_arn` | 레거시 S3 Vectors (빈 값) |
| `vector_index_name`, `vector_index_arn` | 레거시 S3 Vectors 인덱스 |
| `s3_bucket`, `s3_arn` | 문서·세션 저장 S3 버킷 |
| `s3_files_file_system_id` | S3 Files file system ID (session) |
| `s3_files_access_point_arn` | S3 Files access point ARN (session) |
| `agent_runtime_vpc_subnets` | AgentCore Runtime VPC subnet ID 목록 |
| `agent_runtime_security_groups` | AgentCore Runtime security group ID 목록 |
| `sharing_url` | CloudFront URL |
| `cognito_user_pool_id`, `cognito_client_id` | Cognito 인증 |
| `cognito_admin_username`, `cognito_region` | Cognito admin / 리전 |
| `memory_id`, `agentcore_memory_role` | AgentCore Memory |
| `agent_runtime_arn`, `agent_runtime_role` | LangGraph AgentCore Runtime |
| `guardrail_id`, `guardrail_name` | Bedrock Guardrail (Runtime installer) |
| `latest_image_tag`, `build_number` | ECS Web UI 이미지 빌드 태그 |
| `agentcore_websearch_gateway_name` | AgentCore Web Search gateway 이름 |
| `agentcore_websearch_gateway_region` | AgentCore Web Search gateway 리전 (`us-east-1`) |
| `agentcore_websearch_gateway_id` | AgentCore Web Search gateway ID |
| `agentcore_websearch_gateway_url` | AgentCore Web Search gateway URL |
| `agentcore_websearch_gateway_role` | AgentCore Web Search gateway IAM 역할 ARN |

ECS 컨테이너에는 `APP_CONFIG_JSON` 환경변수로 동일한 설정이 주입되며, `docker-entrypoint.sh`가 시작 시 `application/config.json`으로 기록합니다. S3 Files 마운트 시 `TASK_DB_MOUNT=/mnt/app-data`, `TASK_DB_PROJECT={project_name}`도 주입됩니다.

### Docker Container 구성

ECS Web UI는 프로젝트 루트의 multi-stage `Dockerfile`로 빌드됩니다. Agent 추론은 AgentCore Runtime(`runtime_agent/langgraph/Dockerfile`)에서 별도 `linux/arm64` 이미지로 배포됩니다.

빌드 시 `docker buildx build --platform linux/arm64 --push`를 사용하며, Dockerfile 자체에는 `--platform` 지정이 없습니다.

```text
# Stage 1: frontend build
FROM node:22-alpine AS frontend
WORKDIR /web
COPY application/web/package.json application/web/package-lock.json ./
RUN npm ci
COPY application/web/ .
RUN npm run build

# Stage 2: Python runtime
FROM python:3.13-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
RUN pip install fastapi python-multipart uvicorn[standard] boto3 cryptography \
    langchain_aws langchain-openai "openai>=2.41.0" \
    aws-bedrock-token-generator requests
COPY . .
COPY --from=frontend /web/dist /app/application/web/dist
RUN pip install --no-cache-dir -r /app/graph/requirements.txt
RUN pip install --no-cache-dir -r /app/ess/requirements.txt
RUN chmod +x /app/docker-entrypoint.sh \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser
EXPOSE 8501
HEALTHCHECK CMD curl --fail http://localhost:8501/api/health
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "application.server:app", "--host", "0.0.0.0", "--port", "8501", "--no-server-header"]
```

`docker-entrypoint.sh`는 `APP_CONFIG_JSON` 환경변수가 있으면 `/app/application/config.json`을 생성한 뒤 uvicorn을 실행합니다.

### 주의사항
- Docker 이미지 빌드와 ECS Fargate·AgentCore Runtime 모두 **ARM64** 전용입니다. x86 Mac/EC2에서는 `installer.py`와 `runtime_agent/langgraph/installer.py` 모두 실패하므로, t4g/m7g 등 ARM64 EC2에서 실행하세요.
- **Python 3.12 venv**에서 installer를 실행하세요. boto3 >= 1.43.32 필수.
- CloudFront 배포는 완전히 활성화되기까지 15–20분이 소요될 수 있습니다.
- ECS Fargate 서비스가 안정화되고 ALB 헬스체크가 통과하기까지 수 분이 걸릴 수 있습니다.
- `application/config.json`은 부분 배포 실패 시에도 `finally`에서 저장됩니다.
- 기존 EC2 배포에서 생성된 `TG-for-{project_name}` 타겟 그룹이 `instance` 타입이면 ECS 배포 전 삭제가 필요합니다 (Fargate는 `ip` 타입 필요).
- Private Subnet의 Fargate 태스크는 NAT Gateway 및 VPC Endpoint를 통해 ECR에서 이미지를 pull합니다.
- Websearch / web_fetch MCP 사용 시 **NAT Gateway**가 필수입니다.
- OpenAI Mantle 모델(GPT 5.4/5.5) 사용 시 Runtime IAM에 해당 Mantle 리전 권한이 있는지 CloudWatch runtime 로그로 확인하세요 (`bedrock-mantle:CreateInference` 401).
- S3 Files 사용 시 AgentCore Runtime은 **VPC 모드**이며, mount target AZ·SG(2049)가 맞아야 invoke가 성공합니다.
- S3 bucket **versioning Enabled**가 없으면 file system 생성이 실패합니다 (`ValidationException`).
- Managed `sessionStorage`만 사용할 경우 Runtime **Version 업데이트 시** `/mnt/workspace` checkpoint가 초기화됩니다 (S3 Files 권장).
- ALB idle timeout(600초)과 CloudFront origin read timeout(60초)은 서로 다릅니다. 매우 긴 SSE 구간은 CloudFront 60초 제한에 주의하세요.

---

## 에러 처리

스크립트는 다음과 같은 에러를 자동으로 처리합니다:

| 상황 | 처리 방법 |
|------|----------|
| 리소스 이미 존재 | 기존 리소스 재사용 |
| CloudFront / Web Search gateway / Cognito pool 이미 존재 | 기존 리소스 재사용 및 config 반영 |
| Cognito admin 이미 존재 | 비밀번호 입력 생략 |
| 서브넷 부족 | 자동으로 서브넷 생성 |
| CIDR 충돌 | 대체 CIDR 블록 자동 선택 |
| 정책 이미 존재 | 기존 정책 업데이트 |
| ECS 서비스 이미 존재 | 새 태스크 정의로 서비스 업데이트 (`forceNewDeployment`) |
| LangGraph Runtime installer 실패 | 경고 로그 후 ECS 배포는 계속 진행 |
| boto3 < 1.43.32 | `ensure_boto3_supports_agentcore_connector()`에서 pip upgrade 안내 후 종료 |
| 비-ARM64 빌드 호스트 | Docker 빌드 단계에서 즉시 실패 (ARM64 EC2 사용 안내) |
| Docker 디스크 부족 | `_ensure_docker_disk_space()`에서 사전 검사·정리 |
| S3 Files file system 생성 실패 | bucket versioning 미활성 → `_ensure_s3_bucket_versioning_enabled()` 자동 처리 |
| 타임아웃 | 재시도 로직 적용 (CloudFront readiness check 등) |

배포 실패 시 상세한 에러 메시지와 스택 트레이스가 출력되며, 가능한 배포 정보는 `application/config.json`에 저장됩니다.

### S3 Files file system 생성 오류

```
Your bucket must have versioning enabled to create a file system.
```

- `create_s3_bucket()`은 신규 bucket에 versioning **Enabled** 설정
- 기존 bucket은 S3 Files 생성 시 `_ensure_s3_bucket_versioning_enabled()`가 자동 활성화
- sync role(`role-s3files-sync-for-{project_name}`) 및 S3/EventBridge inline policy 확인

---

## 인프라 삭제

ECS/ECR·AgentCore Runtime·Cognito·Memory 리소스를 포함한 전체 인프라 삭제:

```bash
python uninstaller.py
```

Runtime만 삭제:

```bash
cd runtime_agent/langgraph
python uninstaller.py
```

삭제 순서(요약): CloudFront 비활성화 → **AgentCore Runtime** (`runtime_agent/langgraph/uninstaller.py` 위임) → ECS → ALB → EC2(레거시) → NAT → **S3 Files** (access point / mount target / file system / sync role) → VPC → Knowledge Base / OpenSearch Serverless → AgentCore Memory → Gateway / IAM / Cognito User Pool → Secrets Manager (origin header, session signing key, CloudFront signing key) → S3 bucket → CloudFront 완전 삭제 → **`application/config.json`**, `runtime_agent/langgraph/config.json` 정리
