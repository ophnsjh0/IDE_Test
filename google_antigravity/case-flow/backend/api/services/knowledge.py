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
    # 공식 문서 근거는 부가 정보 — 실패해도 지식 생성 자체는 유지
    try:
        enrich_with_references(item)
    except Exception:
        logger.exception("reference enrichment failed for %s", item.knowledge_id)
    return 'created', item


# ------------------------------------------------- 공식 문서 근거 보강

ENRICH_SCHEMA = {
    "type": "object",
    "properties": {
        "relevant": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "note": {"type": "string"},
                },
                "required": ["index", "note"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["relevant"],
    "additionalProperties": False,
}

ENRICH_PROMPT = """당신은 TAC 지식 항목(문제-원인-해결)에 벤더 공식 문서 근거를 붙이는
검수자입니다. 지식 항목과, 벡터 검색으로 찾은 공식 문서 발췌 후보들이 주어집니다.

각 발췌가 이 지식의 해결 조치·원인 설명을 **실제로 뒷받침하거나 배경 설명이 되는지**
판단해, 관련 있는 발췌의 index만 고르세요. 주제만 비슷하고 이 지식과 직접 관련이
없는 발췌는 제외하세요. 관련 발췌가 하나도 없으면 relevant를 빈 배열로 두세요.

- index: 후보 발췌 번호 (주어진 번호 그대로)
- note: 이 발췌가 지식의 어떤 부분을 뒷받침하는지 한 줄 설명 (한국어, 합니다체)"""


def enrich_with_references(item, top_k=5):
    """지식 항목에 공식 문서 근거를 찾아 item.references에 저장한다.

    벡터 검색 후보 → AI가 실제 관련 발췌만 선별 → 코드에서 index 검증
    (존재하지 않는 문서를 지어내는 것을 구조적으로 차단).

    반환: 'enriched' | 'none_relevant' | 'no_candidates' | 'unavailable' | 'failed'
    """
    from . import references as refdocs

    from api.models import ReferenceDocument

    query = ' '.join(filter(None, [
        item.title, item.device_model, item.software_version,
        item.resolution[:300],
    ]))
    # 문서 유형별로 후보를 따로 뽑는다 — 이슈 행처럼 청크 수가 많은 유형이
    # 후보를 독식해 가이드 섹션이 밀려나는 것 방지
    doc_types = list(ReferenceDocument.objects.filter(vendor=item.vendor)
                     .values_list('doc_type', flat=True).distinct())
    try:
        candidates = []
        for doc_type in (doc_types or ['']):
            candidates.extend(refdocs.search(query, vendor=item.vendor,
                                             doc_type=doc_type, top_k=top_k))
    except refdocs.EmbeddingUnavailable:
        return 'unavailable'
    if not candidates:
        item.references = []
        item.save(update_fields=['references', 'updated_at'])
        return 'no_candidates'

    parts = [
        "## 지식 항목",
        f"제목: {item.title}",
        f"장비/버전: {item.device_model} / {item.software_version}",
        f"문제: {item.problem}",
        f"원인: {item.root_cause}",
        f"해결 조치:\n{item.resolution}",
        "\n## 공식 문서 발췌 후보",
    ]
    for i, c in enumerate(candidates):
        parts.append(f"[{i}] {c['document']} {c['pages']}\n{c['text'][:1500]}")
    result = generate_structured(ENRICH_PROMPT, '\n\n'.join(parts), ENRICH_SCHEMA)
    if result is None:
        return 'failed'

    references = []
    for entry in result.get('relevant', []):
        index = entry.get('index')
        if isinstance(index, int) and 0 <= index < len(candidates):
            c = candidates[index]
            references.append({
                'document': c['document'],
                'pages': c['pages'],
                'score': c['score'],
                'note': (entry.get('note') or '')[:300],
            })
    item.references = references
    item.save(update_fields=['references', 'updated_at'])
    return 'enriched' if references else 'none_relevant'
