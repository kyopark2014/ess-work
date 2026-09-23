# Kimi K3 연동 설정

ess-work에서 Moonshot AI **Kimi K3**를 Amazon Bedrock으로 호출하기 위해 적용한 설정 요약입니다.

공식 문서: [Kimi K3 — Amazon Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-moonshot-ai-kimi-k3.html)

---

## 접속 정보 (AWS 문서 기준)

| 항목 | 값 |
|------|-----|
| Provider | Moonshot AI |
| 모델명 (UI) | `Kimi K3` |
| Base model ID | `moonshotai.kimi-k3` |
| **사용 중인 inference profile** | `us.moonshotai.kimi-k3` (US Geo CRIS) |
| Global profile (미사용) | `global.moonshotai.kimi-k3` |
| Endpoint | `bedrock-runtime` (`https://bedrock-runtime.{region}.amazonaws.com`) |
| 권장 API | OpenAI-compatible **Chat Completions** (`/openai/v1`) |
| Context window | 1M tokens |

US Geo(`us.`)를 선택한 이유: 기존 Claude/OpenAI 프로필과 같이 US 리전 간만 라우팅해 데이터 레지던시를 맞추기 위함입니다. Global은 약 10% 저렴하지만 전 세계 commercial Region으로 라우팅됩니다.

---

## 적용된 코드 / 설정

### 1. UI 모델 목록

`application/services/config_service.py`의 `MODELS`에 `"Kimi K3"`를 추가했습니다. 로그인 후 설정/사이드바에서 모델을 선택할 수 있습니다.

### 2. Bedrock 모델 프로필

`application/info.py`와 `runtime_agent/langgraph/info.py`에 동일하게 등록했습니다.

```python
kimi_k3_models = [
    {
        "bedrock_region": "us-west-2",  # Oregon
        "model_type": "kimi",
        "model_id": "us.moonshotai.kimi-k3",
    },
    {
        "bedrock_region": "us-east-1",  # N.Virginia
        "model_type": "kimi",
        "model_id": "us.moonshotai.kimi-k3",
    },
    {
        "bedrock_region": "us-east-2",  # Ohio
        "model_type": "kimi",
        "model_id": "us.moonshotai.kimi-k3",
    },
]
```

- `model_type`: `"kimi"` — Claude/Nova/OpenAI와 구분하는 라우팅 키
- `model_id`: US Geo inference profile ID
- `get_model_info("Kimi K3")`로 위 목록을 반환

### 3. LLM 클라이언트 (`chat.py`)

Runtime(`runtime_agent/langgraph/chat.py`)과 Application(`application/chat.py`) 모두 `_build_kimi_chat()`을 추가했습니다.

- **방식**: Bedrock Runtime OpenAI-compatible Chat Completions
- **base_url**: `https://bedrock-runtime.{region}.amazonaws.com/openai/v1`
- **인증**: `bedrock_data_retention.get_bedrock_bearer_token(region)` (Bedrock bearer token)
- **max_tokens**: `16384`
- **라이브러리**: LangChain `ChatOpenAI`

Converse가 아닌 Chat Completions을 쓰는 이유(AWS 문서):

> Prefer the OpenAI-compatible APIs over Converse. Converse는 이전 턴의 reasoning content가 포함되면 `InternalServerException`이 날 수 있으며, LangChain/Strands Agents 기본 설정에 영향을 줍니다.

LangGraph 멀티턴·툴 호출 에이전트이므로 Chat Completions 경로를 사용합니다.

### 4. Graphify LLM 별칭 (`graph/lib/llm.py`)

| UI / 별칭 | Gateway id | Bedrock id |
|-----------|------------|------------|
| `Kimi K3` | `kimi-k3` | `us.moonshotai.kimi-k3` |

`resolve_bedrock_model_id`에서 `moonshotai.` / `global.` / `.moonshotai.` 접두사도 그대로 통과하도록 허용했습니다.

---

## 사용 전 확인 사항

1. **Bedrock 모델 액세스**: 콘솔에서 Moonshot AI / Kimi K3 모델 액세스(또는 Inference profile)를 활성화했는지 확인합니다.
2. **IAM**: Runtime 역할에 이미 `bedrock:InvokeModel` + `arn:aws:bedrock:*::foundation-model/*`가 있으면 추가 IAM 변경은 보통 필요 없습니다. Bearer token 발급 권한은 Mantle/OpenAI 경로와 동일하게 기존 정책을 사용합니다.
3. **리전**: 요청을 보내는 소스 리전은 `us-west-2` / `us-east-1` / `us-east-2` 중 프로필의 첫 항목(`us-west-2`)이 기본으로 사용됩니다.

---

## 참고: 다른 ID 옵션

| Profile | Model ID | 용도 |
|---------|----------|------|
| US Geo (현재) | `us.moonshotai.kimi-k3` | US 내 라우팅, 데이터 레지던시 |
| Global | `global.moonshotai.kimi-k3` | 전 세계 라우팅, Standard 요금이 더 저렴 |
| In-region base | `moonshotai.kimi-k3` | 문서상 base ID (CRIS 미사용 시) |

Global로 바꾸려면 `info.py`의 `model_id`만 `global.moonshotai.kimi-k3`로 변경하면 됩니다.
