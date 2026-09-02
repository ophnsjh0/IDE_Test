"""해결된 케이스·AI 도우미 대화에서 재사용 가능한 기술 지식(문제-원인-해결)을 AI로 추출한다."""
import fcntl
import logging
import os

from django.conf import settings
from django.utils import timezone

from api.models import Case, KnowledgeItem
from .analyzer import (generate_structured, generate_structured_with_model,
                       knowledge_model_candidates)
from .gmail_sync import SyncInProgress

logger = logging.getLogger(__name__)

# 본문 8칸. environment~related_refs는 예전 문제-원인-해결 3칸 시절 resolution에
# 뭉쳐 있던 것들을 분리한 자리다. 못 채우면 ""로 두고 엔지니어가 화면에서 채운다.
KNOWLEDGE_FIELDS = ['environment', 'problem', 'diagnosis', 'root_cause',
                    'resolution', 'verification', 'caveats', 'related_refs']

KNOWLEDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "has_knowledge": {"type": "boolean"},
        "title": {"type": "string"},
        **{field: {"type": "string"} for field in KNOWLEDGE_FIELDS},
        "device_model": {"type": "string"},
        "software_version": {"type": "string"},
    },
    "required": (["has_knowledge", "title"] + KNOWLEDGE_FIELDS
                 + ["device_model", "software_version"]),
    "additionalProperties": False,
}

# 케이스·대화 추출이 공유하는 본문 작성 규칙. 두 프롬프트에 같은 문장을 두 벌
# 적어두면 한쪽만 고쳐지므로 여기 한 군데서 관리한다.
FIELD_GUIDE = """- title: 한 줄 요약 (최대 80자, 검색될 것을 고려해 증상 또는 시나리오·장비를 담을 것)
- environment: 이 지식이 적용되는 전제 조건 — 구성 방식(예: Standalone/HA/SSL-I),
  토폴로지, 장비 모델, 소프트웨어 버전, 관련 라이선스·모듈. 조건이 드러나지 않으면 "".
- problem: 증상과 문제 상황 — 어떤 조건에서 무엇이 잘못됐는지. 설정 절차·벤더 확답
  유형이면 확인하려던 목표와 질문.
- diagnosis: 원인을 좁혀간 절차 — 어떤 명령·로그·테스트로 무엇을 확인했는지 순서대로.
  실제로 수행한 진단만 적고, 없으면 "".
- root_cause: 밝혀진 근본 원인. **밝혀지지 않았으면 ""로 두세요** — "규명되지
  않았습니다" 같은 문장으로 채우지 말 것.
- resolution: 해결 조치·설정 절차·벤더가 확인해 준 결론을 단계별로.
  **이력에 나온 CLI 명령어·설정 라인·패치 버전을 그대로 포함**할 것. 명령어는 각각
  별도 줄에 두세요. 임시 조치(워크어라운드)와 근본 해결이 모두 있으면 구분해 적을 것.
- verification: 조치가 됐는지 확인하는 방법 — 어떤 명령의 어떤 출력을 보면 되는지,
  무엇이 정상 상태인지. 이력에 없으면 "".
- caveats: 주의사항 — 부작용, 재발 조건, 적용 범위 밖인 상황, 함께 확인해야 할 것.
  없으면 "".
- related_refs: 벤더 버그 ID, 케이스 번호, 문서명 등 참조. 한 줄에 하나씩. 없으면 "".
- device_model: 대상 장비 모델명 원문 그대로. 없으면 "".
- software_version: 대상 소프트웨어 버전 원문 그대로. 없으면 ""."""

# 분량을 숫자로 지시하면 없는 내용을 지어내므로, 목적과 보존 규칙으로만 유도한다.
DEPTH_GUIDE = """작성 기준: 이 지식만 읽고 다른 엔지니어가 같은 상황을 재현하고 조치할 수
있어야 합니다. 요약하지 말고, 이력에 실제로 있는 사실은 빠뜨리지 마세요. 특히 명령어·
설정 라인·로그·에러 문자열·버전 번호는 원문 그대로 옮깁니다. 반대로 이력에 없는 내용을
지어내서는 안 되며, 근거가 없는 필드는 빈 문자열 ""로 둡니다 — 빈 칸은 엔지니어가
직접 채웁니다.

**모든 필드 공통: 고객사명·담당자 이름은 쓰지 마세요.** 지식은 벤더·장비·증상 기준으로
재사용되므로 어느 고객의 케이스였는지는 필요 없습니다. 케이스 참조가 필요하면
벤더 케이스 번호만 남기고 제목에 섞인 고객사명은 지웁니다."""

SYSTEM_PROMPT = """당신은 네트워크 벤더(A10/Arista/HPE Aruba/Juniper) TAC 케이스 이력에서
나중에 비슷한 문제를 만난 엔지니어가 재사용할 수 있는 기술 지식을 추출하는 어시스턴트입니다.

케이스의 요약·메일 이력을 읽고 아래 JSON 필드를 작성하세요. 모든 필드는 한국어(합니다체)로
작성하되, 기술 용어, 제품명, CLI 명령어, 설정 라인, 로그, 버전 문자열은 원문 그대로 유지합니다.

- has_knowledge: 다음 세 유형 중 하나에 해당하면 true.
  ① "문제 → 해결" 지식: 증상과 그에 대한 해결 조치가 케이스 이력에 남은 경우.
  ② 설정 절차/가이드 지식: 특정 시나리오(구성 방식·토폴로지·기능)에 대한 설정 절차나
     동작 방식이 CLI 명령어·설정 라인과 함께 정리된 경우.
  ③ 벤더 확답으로 확정된 기술 사양·제약: 특정 기능의 지원/미지원 여부, 동작 조건,
     호환성·영향도를 벤더가 명확히 답한 경우 — 다른 엔지니어가 같은 질문을 반복하지
     않게 해주는 지식입니다.
  다음은 반드시 false: 단순 공지/알림 메일(보안 권고, EOL, 릴리즈 노트, 뉴스레터 등),
  라이선스 발급·RMA 배송·데모장비 임대·공문 작성 같은 행정 처리, 제품 라인업·가격 문의,
  해결 방법이나 벤더 답변이 이력에 드러나지 않은 케이스, 문의만 있고 답이 없는 케이스,
  원인 규명 없이 종결된 케이스.
  false면 나머지 필드는 빈 문자열 "".
""" + FIELD_GUIDE + "\n\n" + DEPTH_GUIDE

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


def extract_knowledge(case, mark_checked=True):
    """케이스 1건에서 지식을 추출해 KnowledgeItem(draft)으로 저장한다.

    반환: ('created', item) | ('no_knowledge', None) | ('failed', None)
    이미 이 케이스에서 추출한 지식이 있으면 ('exists', 기존 item).
    검토가 끝난 케이스(created/no_knowledge)는 knowledge_checked_at을 찍어
    다음 동기화에서 건너뛴다 — failed는 남겨 재시도되게 한다.

    mark_checked=False: 아직 진행 중인 케이스를 수동 추출할 때 쓴다. 지금
    지식이 없다고 '검토 완료'로 찍어버리면, 나중에 케이스가 해결됐을 때
    자동 동기화가 영영 건너뛰게 된다.
    """
    # 랩 재현 지식도 재현 대상 케이스에 붙는다. 그건 벤더가 해결한 기록이
    # 아니므로 여기서 '이미 추출됨'으로 세면 안 된다 — 케이스 유래 지식이
    # 영영 만들어지지 않는다.
    existing = case.knowledge_items.filter(source='case').first()
    if existing:
        return 'exists', existing

    used_model, result = generate_structured_with_model(
        SYSTEM_PROMPT, build_case_material(case), KNOWLEDGE_SCHEMA,
        models=knowledge_model_candidates())
    if result is None:
        return 'failed', None
    if not result.get('has_knowledge') or not (result.get('resolution') or '').strip():
        if mark_checked:
            _mark_checked(case)
        return 'no_knowledge', None

    item = KnowledgeItem.objects.create(
        case=case,
        source='case',
        vendor=case.vendor,
        title=result['title'][:200],
        device_model=(result['device_model'] or case.device_model)[:100],
        software_version=(result['software_version'] or case.software_version)[:50],
        analyzed_by=used_model,
        # 스키마에 필드를 추가할 때 여기를 같이 고치는 걸 잊지 않도록 목록에서 채운다.
        # get()을 쓰는 이유: 예전 스키마로 만든 목(mock)이나 구모델 응답도 살려둔다.
        **{field: (result.get(field) or '') for field in KNOWLEDGE_FIELDS},
    )
    logger.info("Knowledge extracted from %s -> %s", case.case_id, item.knowledge_id)
    if mark_checked:
        _mark_checked(case)
    # 공식 문서 근거는 부가 정보 — 실패해도 지식 생성 자체는 유지
    try:
        enrich_with_references(item)
    except Exception:
        logger.exception("reference enrichment failed for %s", item.knowledge_id)
    return 'created', item


def _mark_checked(case):
    case.knowledge_checked_at = timezone.now()
    case.save(update_fields=['knowledge_checked_at'])


# ------------------------------------------------- 지식 동기화 (버튼/일괄)

_SYNC_LOCK_FILE = os.path.join(settings.BASE_DIR, '.knowledge_sync.lock')

# 한 번에 처리할 최대 케이스 수 — 케이스당 AI 호출이 있어 HTTP 타임아웃과
# 무료 티어 일일 한도(Gemini ~20건) 안에서 끊는다. 남은 건은 재클릭으로 이어간다.
SYNC_MAX_CASES = 10


def sync_from_cases(limit=SYNC_MAX_CASES):
    """미검토 Resolved 케이스에서 지식을 일괄 추출한다 (지식 동기화 버튼).

    Gmail 동기화와 같은 파일 잠금으로 동시 실행을 차단한다.
    반환: {'scanned', 'created', 'no_knowledge', 'failed', 'remaining'}
    """
    lock_file = open(_SYNC_LOCK_FILE, 'w')
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        raise SyncInProgress('지식 동기화가 이미 진행 중입니다. 잠시 후 다시 시도하세요.')
    try:
        return _sync_from_cases(limit)
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


def _sync_from_cases(limit):
    # 케이스 유래 지식이 아직 없는 것만 — 랩 재현 지식이 붙어 있어도
    # 벤더 이력에서 뽑을 것은 따로 있다 (extract_knowledge와 같은 기준)
    pending = (Case.objects
               .filter(status='Resolved', knowledge_checked_at__isnull=True)
               .exclude(knowledge_items__source='case')
               .prefetch_related('emails').order_by('id'))
    cases = list(pending[:limit])

    summary = {'scanned': len(cases), 'created': 0, 'no_knowledge': 0, 'failed': 0}
    for case in cases:
        outcome, _ = extract_knowledge(case)
        # exists는 쿼리셋 조건상 나올 수 없지만, 경쟁 상황을 대비해 안전 처리
        summary[outcome if outcome != 'exists' else 'no_knowledge'] += 1
    # failed는 checked_at이 안 찍혀 남는다 — 재클릭 시 재시도 대상
    summary['remaining'] = pending.count()
    return summary


# ------------------------------------------------- AI 도우미 대화에서 추출

# 케이스 추출과 달리 벤더가 컨텍스트에 없어 AI가 대화에서 판별한다.
# "Unknown" = 대화에 벤더 단서가 없음 (추출 거부 사유).
# 빈 문자열 센티널은 쓰지 않는다 — Gemini가 enum의 빈 값을 400으로 거부.
CHAT_KNOWLEDGE_SCHEMA = {
    **KNOWLEDGE_SCHEMA,
    "properties": {
        **KNOWLEDGE_SCHEMA["properties"],
        "vendor": {"type": "string",
                   "enum": [v for v, _ in Case.VENDOR_CHOICES] + ["Unknown"]},
    },
    "required": KNOWLEDGE_SCHEMA["required"] + ["vendor"],
}

CHAT_SYSTEM_PROMPT = """당신은 네트워크 벤더(A10/Arista/HPE Aruba/Juniper) 기술지원 AI와
엔지니어가 나눈 기술 대화에서, 나중에 다른 엔지니어가 재사용할 수 있는 기술 지식을
추출하는 어시스턴트입니다.

대화 전체를 읽고 아래 JSON 필드를 작성하세요. 모든 필드는 한국어(합니다체)로 작성하되,
기술 용어, 제품명, CLI 명령어, 설정 라인, 로그, 버전 문자열은 원문 그대로 유지합니다.

중요: 대화에는 시행착오가 섞여 있습니다. 중간에 나온 오답·기각된 가설은 버리고,
**최종적으로 유효하다고 확인된 결론만** 추출하세요.

- has_knowledge: 다음 두 유형 중 하나에 해당하면 true.
  ① "문제 → 해결" 지식: 증상과 그에 대한 해결 조치가 오간 대화.
  ② 설정 절차/가이드 지식: 특정 시나리오(구성 방식·토폴로지·기능)에 대한 구체적인
     설정 절차가 CLI 명령어·설정 라인과 함께 정리된 대화. 특히 답변이 공식 문서
     검색을 근거로 했다면 재사용 가치가 높습니다.
  다음은 반드시 false: 단순 현황/통계 질문(케이스 몇 건 등), 리포트 생성 요청,
  구체적 설정 없이 개념만 오간 일반 상식 문답, 결론이 나지 않은 대화.
  false면 나머지 필드는 빈 문자열 "".
- vendor: 대화에서 다룬 장비의 벤더. 대화에 단서가 없으면 "Unknown".
""" + FIELD_GUIDE + "\n\n" + DEPTH_GUIDE


def build_chat_material(session):
    """추출 프롬프트에 넣을 대화 텍스트를 구성한다.

    답변이 어떤 도구(케이스/문서/웹 검색)를 근거로 했는지도 포함한다 —
    추출 모델이 근거 있는 결론과 일반 추측을 구분하는 데 쓰인다.
    """
    parts = []
    remaining = _MAX_CONTEXT_CHARS
    for turn in session.turns.all():
        speaker = '엔지니어' if turn.role == 'user' else 'AI'
        tools = ', '.join(t.get('name', '') for t in (turn.tool_calls or []))
        header = f"[{speaker}]" + (f" (사용 도구: {tools})" if tools else '')
        body = turn.content[:6000]
        if turn.attachments:
            # 첨부만 올린 턴은 본문이 비어 있어, 파일명이라도 없으면 맥락이 끊긴다
            names = ', '.join(a.get('filename') or '?' for a in turn.attachments)
            body = f"{body}\n[첨부 파일: {names}]".strip()
        entry = f"{header}\n{body}"
        if remaining - len(entry) < 0:
            break
        remaining -= len(entry)
        parts.append(entry)
    return '\n\n'.join(parts)


def extract_knowledge_from_chat(session):
    """AI 도우미 대화 1건에서 지식을 추출해 KnowledgeItem(draft)으로 저장한다.

    반환: ('created', item) | ('no_knowledge', None) | ('no_vendor', None)
    | ('failed', None) | ('exists', 기존 item)
    """
    existing = session.knowledge_items.first()
    if existing:
        return 'exists', existing

    used_model, result = generate_structured_with_model(
        CHAT_SYSTEM_PROMPT, build_chat_material(session), CHAT_KNOWLEDGE_SCHEMA,
        models=knowledge_model_candidates())
    if result is None:
        return 'failed', None
    if not result.get('has_knowledge') or not (result.get('resolution') or '').strip():
        return 'no_knowledge', None
    vendor = result.get('vendor') or ''
    if vendor not in dict(Case.VENDOR_CHOICES):
        return 'no_vendor', None

    item = KnowledgeItem.objects.create(
        chat_session=session,
        source='chat',
        vendor=vendor,
        title=result['title'][:200],
        device_model=result['device_model'][:100],
        software_version=result['software_version'][:50],
        analyzed_by=used_model,
        **{field: (result.get(field) or '') for field in KNOWLEDGE_FIELDS},
    )
    logger.info("Knowledge extracted from chat session %s -> %s",
                session.id, item.knowledge_id)
    # 공식 문서 근거는 부가 정보 — 실패해도 지식 생성 자체는 유지
    try:
        enrich_with_references(item)
    except Exception:
        logger.exception("reference enrichment failed for %s", item.knowledge_id)
    return 'created', item



# ------------------------------------------------- 랩 재현 -> 지식

RUN_KNOWLEDGE_SCHEMA = CHAT_KNOWLEDGE_SCHEMA

RUN_SYSTEM_PROMPT = """당신은 네트워크 랩(EVE-NG)에서 실제로 실행하고 검증한 테스트
기록에서, 나중에 다른 엔지니어가 재사용할 수 있는 기술 지식을 추출하는 어시스턴트입니다.

기록 전체를 읽고 아래 JSON 필드를 작성하세요. 모든 필드는 한국어(합니다체)로 작성하되,
기술 용어, 제품명, CLI 명령어, 설정 라인, 로그, 버전 문자열은 원문 그대로 유지합니다.

이 기록의 성격을 정확히 이해하세요. **여기 적힌 명령은 실제로 장비에 들어갔고, 검증
명령의 출력으로 통과 판정을 받은 것입니다.** 그러므로 추측하지 말고 기록에 있는 것만
쓰면 됩니다. 반대로 기록에 없는 배경 설명을 지어내지 마세요.

랩은 실제 운영 환경이 아닙니다 — 이 점은 caveats에 반드시 남기세요.

- has_knowledge: 검증에 통과한 설정 절차가 기록에 있으면 true. 통과한 검증이 하나도
  없거나 적용한 명령이 없으면 false. false면 나머지 필드는 빈 문자열 "".
- vendor: 대상 장비의 벤더. 기록에 단서가 없으면 "Unknown".
""" + FIELD_GUIDE + """

랩 기록에서 각 필드를 채우는 법:
- environment: 랩 토폴로지와 장비 구성, 역할 매핑. 이 절차가 어떤 구성에서 통했는지.
- problem: 이 랩으로 확인하려던 것. 재현 대상 케이스가 있으면 그 증상.
- diagnosis: 사전 점검과 검증 단계에서 무엇을 어떤 명령으로 확인했는지.
- resolution: **실제로 장비에 넣은 명령을 순서대로 그대로.** 요약하지 마세요.
- verification: 검증에 쓴 명령과, 무엇이 보이면 정상인지.
- caveats: 되돌리는 명령(롤백)과, 이것이 랩에서 검증된 결과라는 사실.

""" + DEPTH_GUIDE

# 드라이버는 곧 벤더다 — 모델에게 묻는 것보다 이쪽이 정확하다
_DRIVER_VENDOR = {'a10_axapi': 'A10', 'arista_eapi': 'Arista'}


def _run_vendor(run, ai_vendor=''):
    """이 실행의 벤더. 케이스 > 드라이버 > 모델 판단 순."""
    if run.case:
        return run.case.vendor
    touched = {a.node_name for a in run.applied.all()}
    vendors = {_DRIVER_VENDOR[a.driver]
               for a in run.lab.accesses.all()
               if a.node_name in touched and a.driver in _DRIVER_VENDOR}
    # 한 벤더만 건드렸을 때만 확정한다. 여러 벤더가 섞였으면 어느 쪽 지식인지
    # 기록만으로는 정할 수 없으니 모델 판단으로 넘긴다.
    if len(vendors) == 1:
        return vendors.pop()
    return ai_vendor


def build_run_material(run):
    """추출 프롬프트에 넣을 랩 실행 기록을 구성한다.

    무엇을 재현하려 했는지(케이스) · 어떤 구성에서(토폴로지·역할) · 무엇을
    넣었고(적용 원장) · 무엇으로 확인했는지(단계 기록)를 한 덩어리로 만든다.
    """
    parts = [f"랩: {run.lab.name}", f"시나리오: {run.blueprint.name}"]
    if run.blueprint.description:
        parts.append(f"시나리오 설명: {run.blueprint.description}")
    if run.case:
        parts.append(f"재현 대상 케이스: {run.case.case_id} [{run.case.vendor}] "
                     f"{run.case.summary}")
        if run.case.description:
            parts.append(f"케이스 문제 설명: {run.case.description[:4000]}")

    accesses = {a.node_name: a for a in run.lab.accesses.all()}
    nodes = []
    for node in run.lab.nodes.all():
        access = accesses.get(node.name)
        role = f", 역할 {access.role}" if access and access.role else ''
        driver = f", 드라이버 {access.driver}" if access else ''
        # display_name은 EVE-NG 화면에 뜨는 이름 — 이름이 겹칠 때만 키와 다르다
        label = node.display_name or node.name
        nodes.append(f"- {label} ({node.template or node.image or '?'}{role}{driver})")
    if nodes:
        parts.append("=== 랩 구성 ===\n" + "\n".join(nodes))

    links = [f"- {l.source} {l.source_port} ↔ {l.target} {l.target_port}"
             for l in run.lab.links.all()
             if not l.source_is_network and not l.target_is_network]
    if links:
        parts.append("=== 장비 간 배선 ===\n" + "\n".join(links))

    applied = []
    for obj in run.applied.all():
        applied.append(f"[{obj.node_name}] 적용: " + ' / '.join(obj.commands))
        if obj.rollback_commands:
            applied.append(f"[{obj.node_name}] 되돌리기: "
                           + ' / '.join(obj.rollback_commands))
    if applied:
        parts.append("=== 실제로 장비에 보낸 명령 ===\n" + "\n".join(applied))

    steps = [f"- [{s.phase}/{s.status}] {s.label}"
             + (f" ({s.node_name})" if s.node_name else '')
             + (f"\n  {s.detail[:2000]}" if s.detail else '')
             for s in run.steps.all()]
    parts.append("=== 실행 단계와 결과 ===\n" + "\n".join(steps))
    return "\n".join(parts)


def extract_knowledge_from_run(run):
    """랩 실행 1건에서 지식을 추출해 KnowledgeItem(draft)으로 저장한다.

    반환: ('created', item) | ('exists', 기존 item) | ('not_verified', None)
    | ('no_knowledge', None) | ('no_vendor', None) | ('failed', None)

    **통과하지 못한 실행은 추출하지 않는다.** "이 방법으로는 안 되더라"도
    값진 기록이지만, 그건 AI가 요약할 게 아니라 돌려본 사람이 직접 적어야
    한다 — 왜 안 됐는지는 기록에 안 남고 사람 머릿속에만 있다.
    """
    existing = run.knowledge_items.first()
    if existing:
        return 'exists', existing
    from . import lab_runner
    if not lab_runner.succeeded(run):
        return 'not_verified', None

    used_model, result = generate_structured_with_model(
        RUN_SYSTEM_PROMPT, build_run_material(run), RUN_KNOWLEDGE_SCHEMA,
        models=knowledge_model_candidates())
    if result is None:
        return 'failed', None
    if not result.get('has_knowledge') or not (result.get('resolution') or '').strip():
        return 'no_knowledge', None
    vendor = _run_vendor(run, result.get('vendor') or '')
    if vendor not in dict(Case.VENDOR_CHOICES):
        return 'no_vendor', None

    item = KnowledgeItem.objects.create(
        lab_run=run,
        # 케이스 재현이어도 출처는 lab이다 — 벤더가 해결한 기록이 아니라
        # 우리 랩에서 돌려본 결과이므로 신뢰도가 한 단계 아래다.
        case=run.case,
        source='lab',
        vendor=vendor,
        title=result['title'][:200],
        device_model=result['device_model'][:100],
        software_version=result['software_version'][:50],
        analyzed_by=used_model,
        **{field: (result.get(field) or '') for field in KNOWLEDGE_FIELDS},
    )
    logger.info("Knowledge extracted from lab run %s -> %s", run.id, item.knowledge_id)
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
    result = generate_structured(ENRICH_PROMPT, '\n\n'.join(parts), ENRICH_SCHEMA,
                                 models=knowledge_model_candidates())
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
