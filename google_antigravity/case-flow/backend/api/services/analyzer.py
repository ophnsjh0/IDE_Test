"""Claude 기반 케이스 메일 분석: 번역 + 내용 정리 + 상태 판단을 한 번의 호출로 수행."""
import json
import logging
import time

import anthropic
from django.conf import settings

logger = logging.getLogger(__name__)

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "subject_ko": {"type": "string"},
        "body_ko": {"type": "string"},
        "summary": {"type": "string"},
        "description": {"type": "string"},
        "action_update": {"type": "string"},
        "resolution": {"type": "string"},
        "suggested_status": {"type": "string", "enum": ["Open", "Pending", "Resolved"]},
        "device_model": {"type": "string"},
        "device_serial": {"type": "string"},
        "software_version": {"type": "string"},
    },
    "required": ["subject_ko", "body_ko", "summary", "description",
                 "action_update", "resolution", "suggested_status",
                 "device_model", "device_serial", "software_version"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """당신은 네트워크 벤더(A10/Arista/HPE Aruba/Juniper) TAC 케이스 메일을 분석해
사내 케이스 관리 시스템에 등록할 내용을 정리하는 어시스턴트입니다.

영문 메일을 읽고 아래 JSON 필드를 작성하세요. 모든 필드는 한국어(합니다체)로 작성하되,
기술 용어, 제품명, CLI 명령어, 로그 라인, 케이스 번호, 버전 문자열은 원문 그대로 유지합니다.

- subject_ko: 메일 제목의 한글 번역
- body_ko: 메일 본문의 완전한 한글 번역. 원문의 줄바꿈과 구조를 유지. 요약하지 말 것.
- summary: 이 케이스가 무엇에 관한 것인지 한 줄 요약 (최대 80자)
- description: 문제 상황 정리 — 증상, 대상 장비/모델, 소프트웨어 버전, 발생 시점, 영향 범위를
  파악 가능한 범위에서 간결히 정리. 신규 케이스 등록용.
- action_update: **이번 메일에서** 새로 진행/요청/답변된 내용만 2~4줄로 요약.
  (예: 벤더가 RMA 승인, 로그 수집 요청, 패치 버전 안내, 당사가 로그 전달 등)
  기존 케이스 이력을 반복하지 말 것.
- resolution: 이 메일에 해결 방법/원인/조치 완료 내용이 포함된 경우에만 그 내용을 정리.
  해결 내용이 없으면 빈 문자열 "".
- suggested_status: 케이스 이력과 이번 메일을 종합한 현재 상태 판단.
  - "Resolved": 벤더가 해결을 확인했거나 케이스 종료가 통보된 경우
  - "Pending": 상대방의 회신·정보 제공·조치를 기다리는 상태 (예: 벤더가 로그를 요청하고 대기 중)
  - "Open": 그 외 진행 중인 상태
- device_model: 이 케이스의 대상 장비 모델명 원문 그대로 (예: TH1040-F, DCS-7050SX3-48YC12, Aruba 7205).
  메일에 없으면 "".
- device_serial: 대상 장비의 시리얼 번호 원문 그대로. 여러 개면 쉼표로 나열 (최대 5개). 없으면 "".
- software_version: 대상 장비의 소프트웨어/OS 버전 원문 그대로 (예: 6.0.8-SP1, ACOS 5.2.1-P10이면 5.2.1-P10,
  EOS 4.32.4M이면 4.32.4M). 없으면 ""."""


# 프론트/설정 API에서 선택 가능한 모델 카탈로그.
AVAILABLE_MODELS = [
    {'id': 'claude-opus-4-8',       'provider': 'anthropic', 'note': '$5/$25 — 최고 품질'},
    {'id': 'claude-sonnet-5',       'provider': 'anthropic', 'note': '$3/$15 — 중간'},
    {'id': 'claude-haiku-4-5',      'provider': 'anthropic', 'note': '$1/$5 — 저비용'},
    {'id': 'gpt-5.5',               'provider': 'openai',    'note': '$5/$30 — 최고 품질'},
    {'id': 'gpt-5.4',               'provider': 'openai',    'note': '$2.5/$15 — 중간'},
    {'id': 'gpt-5.4-nano',          'provider': 'openai',    'note': '$0.2/$1.25 — 저비용'},
    {'id': 'gemini-3.5-flash',      'provider': 'google',    'note': '무료 티어 — Flash'},
    {'id': 'gemini-3.1-flash-lite', 'provider': 'google',    'note': '무료 티어 — Flash-Lite'},
]

TRANSLATION_MODEL_SETTING_KEY = 'translation_model'


def get_translation_model():
    """사용할 모델 결정: DB 저장값(프론트에서 선택) → settings.TRANSLATION_MODEL 순."""
    try:
        from api.models import AppSetting
        return AppSetting.get(TRANSLATION_MODEL_SETTING_KEY) or settings.TRANSLATION_MODEL
    except Exception:  # 마이그레이션 전 등 DB 미준비 시 설정값 사용
        return settings.TRANSLATION_MODEL


def detect_provider(model):
    """모델 이름 접두어로 제공자 판별 (claude-/gpt-/gemini-)."""
    if model.startswith('gpt') or model.startswith('o'):
        return 'openai'
    if model.startswith('gemini'):
        return 'google'
    return 'anthropic'


def provider_api_key(provider):
    return {
        'anthropic': settings.ANTHROPIC_API_KEY,
        'openai': settings.OPENAI_API_KEY,
        'google': settings.GOOGLE_API_KEY,
    }[provider]


def _build_user_content(subject, body, direction, is_new_case, case_context=''):
    """제공자 공통 사용자 프롬프트 생성."""
    body = (body or '')[:30000]
    direction_label = '벤더로부터 수신한 메일' if direction == 'inbound' else '당사가 벤더에게 보낸 메일'

    parts = []
    if is_new_case:
        parts.append("이 메일은 새 케이스의 첫 메일입니다.")
    else:
        parts.append("이 메일은 기존 케이스에 추가된 메일입니다. 기존 케이스 이력:")
        parts.append(f"<case_context>\n{case_context}\n</case_context>")
    parts.append(f"메일 방향: {direction_label}")
    parts.append(f"<subject>{subject}</subject>")
    parts.append(f"<body>\n{body}\n</body>")
    return '\n\n'.join(parts)


def _build_request_params(subject, body, direction, is_new_case, case_context=''):
    """Anthropic Messages API 파라미터 생성 (동기/배치 공용)."""
    return {
        "model": get_translation_model(),
        "max_tokens": 16000,
        "system": SYSTEM_PROMPT,
        "output_config": {
            "format": {"type": "json_schema", "schema": ANALYSIS_SCHEMA}
        },
        "messages": [{
            "role": "user",
            "content": _build_user_content(subject, body, direction, is_new_case, case_context),
        }],
    }


def _parse_response(response, subject=''):
    """Messages API 응답에서 분석 JSON을 추출. 거부/파싱 실패 시 None."""
    if response.stop_reason == "refusal":
        logger.warning("Analysis request refused for subject: %s", subject)
        return None
    try:
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)
    except Exception:
        logger.exception("Failed to parse analysis response for subject: %s", subject)
        return None


def _analyze_anthropic(subject, body, direction, is_new_case, case_context=''):
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = client.messages.create(
        **_build_request_params(subject, body, direction, is_new_case, case_context)
    )
    return _parse_response(response, subject)


def _analyze_openai(subject, body, direction, is_new_case, case_context=''):
    from openai import OpenAI

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=get_translation_model(),
        max_completion_tokens=16000,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",
             "content": _build_user_content(subject, body, direction, is_new_case, case_context)},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "case_analysis", "schema": ANALYSIS_SCHEMA, "strict": True},
        },
    )
    return json.loads(response.choices[0].message.content)


def _gemini_schema(schema):
    """Gemini responseSchema는 additionalProperties를 지원하지 않으므로 제거."""
    if isinstance(schema, dict):
        return {k: _gemini_schema(v) for k, v in schema.items() if k != 'additionalProperties'}
    if isinstance(schema, list):
        return [_gemini_schema(item) for item in schema]
    return schema


def _analyze_gemini(subject, body, direction, is_new_case, case_context=''):
    from google import genai

    client = genai.Client(api_key=settings.GOOGLE_API_KEY)
    response = client.models.generate_content(
        model=get_translation_model(),
        contents=_build_user_content(subject, body, direction, is_new_case, case_context),
        config={
            "system_instruction": SYSTEM_PROMPT,
            "response_mime_type": "application/json",
            "response_schema": _gemini_schema(ANALYSIS_SCHEMA),
            "max_output_tokens": 16000,
        },
    )
    return json.loads(response.text)


_PROVIDER_ANALYZERS = {
    'anthropic': _analyze_anthropic,
    'openai': _analyze_openai,
    'google': _analyze_gemini,
}


def analyze_email(subject, body, direction, is_new_case, case_context=''):
    """메일 1건을 번역+분석. 실패 또는 API 키 미설정 시 None을 반환하고,
    호출 측은 원문 그대로 저장하는 폴백으로 처리한다.

    settings.TRANSLATION_MODEL의 접두어(claude-/gpt-/gemini-)에 따라
    Anthropic / OpenAI / Google Gemini API로 자동 라우팅된다."""
    provider = detect_provider(get_translation_model())
    if not provider_api_key(provider):
        logger.warning("API key for %s not set; skipping analysis.", provider)
        return None

    try:
        return _PROVIDER_ANALYZERS[provider](subject, body, direction, is_new_case, case_context)
    except Exception:
        logger.exception("Email analysis failed (%s/%s) for subject: %s",
                         provider, get_translation_model(), subject)
        return None


def analyze_emails_batch(requests_by_id, poll_interval=15, timeout=3600):
    """여러 메일을 Message Batches API로 일괄 분석 (토큰 비용 50% 할인).

    requests_by_id: {custom_id: analyze_email과 동일한 kwargs dict}
    반환: {custom_id: 분석 결과 dict 또는 None}
    실시간성이 없는 재분석/일괄 작업 전용 — 대부분 1시간 내에 완료된다.
    """
    results = {custom_id: None for custom_id in requests_by_id}
    if not requests_by_id:
        return results

    # Batch API(50% 할인)는 Anthropic 전용 — 다른 제공자 모델이면 순차 호출로 폴백.
    if detect_provider(get_translation_model()) != 'anthropic':
        logger.info("Batch API unavailable for %s; falling back to sequential calls.",
                    get_translation_model())
        return {custom_id: analyze_email(**kwargs)
                for custom_id, kwargs in requests_by_id.items()}

    if not settings.ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set; skipping batch analysis.")
        return results

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    try:
        batch = client.messages.batches.create(requests=[
            {"custom_id": custom_id, "params": _build_request_params(**kwargs)}
            for custom_id, kwargs in requests_by_id.items()
        ])
        deadline = time.monotonic() + timeout
        while True:
            batch = client.messages.batches.retrieve(batch.id)
            if batch.processing_status == "ended":
                break
            if time.monotonic() > deadline:
                logger.error("Batch %s did not finish within %ss", batch.id, timeout)
                return results
            time.sleep(poll_interval)

        for result in client.messages.batches.results(batch.id):
            if result.result.type == "succeeded":
                results[result.custom_id] = _parse_response(result.result.message)
            else:
                logger.warning("Batch item %s failed: %s", result.custom_id, result.result.type)
        return results
    except Exception:
        logger.exception("Batch email analysis failed")
        return results
