"""해결된 케이스에서 재사용 가능한 기술 지식(문제-원인-해결)을 AI로 추출한다."""
import logging

from api.models import KnowledgeItem
from .analyzer import generate_structured, get_translation_model

logger = logging.getLogger(__name__)

KNOWLEDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "has_knowledge": {"type": "boolean"},
        "title": {"type": "string"},
        "problem": {"type": "string"},
        "root_cause": {"type": "string"},
        "resolution": {"type": "string"},
        "device_model": {"type": "string"},
        "software_version": {"type": "string"},
    },
    "required": ["has_knowledge", "title", "problem", "root_cause",
                 "resolution", "device_model", "software_version"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """당신은 네트워크 벤더(A10/Arista/HPE Aruba/Juniper) TAC 케이스 이력에서
나중에 비슷한 문제를 만난 엔지니어가 재사용할 수 있는 기술 지식을 추출하는 어시스턴트입니다.

케이스의 요약·메일 이력을 읽고 아래 JSON 필드를 작성하세요. 모든 필드는 한국어(합니다체)로
작성하되, 기술 용어, 제품명, CLI 명령어, 설정 라인, 로그, 버전 문자열은 원문 그대로 유지합니다.

- has_knowledge: 이 케이스에 재사용 가치가 있는 "문제 → 해결" 지식이 있으면 true.
  다음은 반드시 false: 단순 공지/알림 메일(보안 권고, EOL, 릴리즈 노트 등), 라이선스 발급·RMA
  배송 같은 행정 처리, 해결 방법이 이력에 드러나지 않은 케이스, 문의만 있고 답이 없는 케이스.
  false면 나머지 필드는 빈 문자열 "".
- title: 문제를 한 줄로 요약 (최대 80자, 검색될 것을 고려해 증상·장비를 담을 것)
- problem: 증상과 문제 상황 — 어떤 조건에서 무엇이 잘못됐는지. 고객사명은 쓰지 말 것.
- root_cause: 밝혀진 근본 원인. 벤더가 명확히 밝히지 않았으면 빈 문자열 "".
- resolution: 해결 조치를 단계별로. **실제 사용한 CLI 명령어·설정 변경·패치 버전을 그대로 포함**할
  것 — 이 필드가 지식의 핵심 가치입니다. 명령어는 각각 별도 줄에 두세요.
- device_model: 대상 장비 모델명 원문 그대로. 없으면 "".
- software_version: 문제가 발생한 소프트웨어 버전 원문 그대로. 없으면 ""."""

# 케이스당 컨텍스트에 넣을 메일 본문 상한 (오래된 순으로 자름)
_MAX_CONTEXT_CHARS = 40000


def build_case_material(case):
    """추출 프롬프트에 넣을 케이스 이력 텍스트를 구성한다."""
    parts = [
        f"벤더: {case.vendor}",
        f"케이스 요약: {case.summary}",
        f"문제 설명: {case.description or ''}",
        f"진행 이력: {case.action_steps or ''}",
        f"해결 내용: {case.resolution or ''}",
        f"장비: {case.device_model} / 버전: {case.software_version}",
    ]
    remaining = _MAX_CONTEXT_CHARS
    emails = []
    # 해결 내용은 보통 뒤쪽 메일에 있으므로 최신 메일부터 예산을 배분한다
    for email in case.emails.order_by('-received_at'):
        body = email.body_ko or email.body_original or ''
        entry = (f"--- 메일 ({email.received_at:%Y-%m-%d}, "
                 f"{'벤더→당사' if email.direction == 'inbound' else '당사→벤더'}) ---\n"
                 f"제목: {email.subject}\n{body[:6000]}")
        if remaining - len(entry) < 0:
            break
        remaining -= len(entry)
        emails.append(entry)
    parts.append("\n\n=== 메일 이력 (과거순) ===\n" + "\n\n".join(reversed(emails)))
    return "\n".join(parts)


def extract_knowledge(case):
    """케이스 1건에서 지식을 추출해 KnowledgeItem(draft)으로 저장한다.

    반환: ('created', item) | ('no_knowledge', None) | ('failed', None)
    이미 이 케이스에서 추출한 지식이 있으면 ('exists', 기존 item).
    """
    existing = case.knowledge_items.first()
    if existing:
        return 'exists', existing

    result = generate_structured(SYSTEM_PROMPT, build_case_material(case), KNOWLEDGE_SCHEMA)
    if result is None:
        return 'failed', None
    if not result.get('has_knowledge') or not (result.get('resolution') or '').strip():
        return 'no_knowledge', None

    item = KnowledgeItem.objects.create(
        case=case,
        vendor=case.vendor,
        title=result['title'][:200],
        problem=result['problem'],
        root_cause=result['root_cause'],
        resolution=result['resolution'],
        device_model=(result['device_model'] or case.device_model)[:100],
        software_version=(result['software_version'] or case.software_version)[:50],
        analyzed_by=get_translation_model(),
    )
    logger.info("Knowledge extracted from %s -> %s", case.case_id, item.knowledge_id)
    return 'created', item
