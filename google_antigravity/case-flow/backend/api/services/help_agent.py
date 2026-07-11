"""헬프 에이전트 — 트리아지 + 역할별 에이전트.

구조(2026-07-11 합의):
  사용자 질문 → 트리아지(규칙 우선, 애매하면 haiku 분류)
      ├→ ① DB 검색 에이전트 (haiku)  — 케이스 조회·유사 사례
      └→ ③ 리포팅 에이전트 (sonnet) — 최근 근황 정리·현황 보고서
  (② 기술지원(웹 검색)은 추후 — 검색어 보안 정제 필요)

답변 검증은 LLM 평가자 대신 코드 검증 — 응답에 인용된 C-번호가 실제
DB에 존재하는지 확인하고, 없는 번호는 경고를 덧붙인다.
"""
import json
import logging
import re
from datetime import timedelta

import anthropic
from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

from ..models import Case

logger = logging.getLogger(__name__)

# 도구 호출 왕복 상한 — 검색→상세 조회 두어 번이면 충분하고, 폭주 방지
MAX_TOOL_ITERATIONS = 6
# 프론트가 보내는 대화 이력 상한 (오래된 턴은 잘라 토큰 낭비 방지)
MAX_HISTORY_MESSAGES = 20

SEARCH_SYSTEM_PROMPT = """당신은 Case-Flow(벤더 TAC 케이스 관리 시스템)의 도우미입니다.
사용자는 네트워크 엔지니어이며, A10/Arista/HPE Aruba/Juniper 벤더의
기술지원 케이스 이력에 대해 질문합니다.

규칙:
- 케이스에 대한 질문에는 반드시 도구로 DB를 조회한 뒤, 조회 결과에 근거해 답하세요.
- 도구 결과에 없는 케이스 번호나 내용을 지어내지 마세요. 결과가 없으면 없다고 답하세요.
- 케이스를 언급할 때는 항상 C-번호(예: C-1122)를 함께 표기하세요.
- 유사 사례를 찾을 때는 증상 키워드(예: failover, VRRP, 파티션)로 검색하고,
  장비 모델이 주어지면 device 필터를 활용하세요.
- 한국어로 간결하게 답하세요. 표가 유용하면 마크다운 표를 사용하세요.
- 이 시스템의 케이스 데이터 범위를 벗어나는 일반 기술 질문에는
  "케이스 이력 검색 도우미라 일반 기술 지원은 범위 밖"이라고 안내하세요."""

REPORT_SYSTEM_PROMPT = """당신은 Case-Flow(벤더 TAC 케이스 관리 시스템)의 리포팅 담당입니다.
사용자가 최근 케이스 근황·현황 정리를 요청하면, 도구로 데이터를 수집한 뒤
바로 공유할 수 있는 한국어 보고서를 작성합니다.

작성 규칙:
- 반드시 get_case_stats와 list_recent_cases로 데이터를 먼저 수집하세요.
  주요 케이스는 필요 시 get_case_detail로 내용을 확인하세요.
- 모든 숫자는 도구 결과에 있는 값만 사용하세요. 추정하거나 지어내지 마세요.
- 케이스를 언급할 때는 항상 C-번호를 표기하세요.
- 구성: ## 제목(기간 명시) → 요약(2-3문장) → 전체 지표 → 벤더별 현황(표)
  → 주요 케이스(진행 중/최근 해결, 각 1-2줄) → 조치 필요 사항.
- 기간이 명시되지 않으면 최근 30일 기준으로 작성하고 그 사실을 밝히세요."""

# 트리아지: 명백한 리포트 요청은 규칙으로 즉시 분기 (LLM 호출 절약)
REPORT_KEYWORDS = ('리포트', '레포트', '보고서', 'report', '주간', '월간',
                   '브리핑', '근황', '현황 정리', '정리해', '보고해')

TRIAGE_SYSTEM_PROMPT = """사용자 질문을 두 담당 중 하나로 분류하는 분류기입니다.
- report: 여러 케이스의 근황·현황을 정리한 보고서/브리핑 작성 요청
- search: 특정 케이스 조회, 유사 사례 검색, 단건 질문 등 그 외 전부
반드시 search 또는 report 한 단어로만 답하세요."""

_SEARCH_TOOL_DEFS = {
    'search_cases': {
        'name': 'search_cases',
        'description': (
            '케이스 DB를 검색한다. query는 요약/설명/장비모델/시리얼/벤더케이스번호에 '
            '대한 키워드 부분일치(공백으로 여러 키워드 AND). C-1122 같은 케이스 번호도 '
            'query로 넣으면 해당 케이스를 찾는다. 사용자가 케이스를 찾거나 유사 사례를 '
            '물으면 먼저 이 도구를 호출할 것.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'query': {'type': 'string', 'description': '검색 키워드 (증상, 장비명, 케이스번호 등)'},
                'vendor': {'type': 'string', 'enum': ['A10', 'Arista', 'HPE Aruba', 'Juniper'],
                           'description': '벤더 필터 (선택)'},
                'status': {'type': 'string', 'enum': ['Open', 'Resolved', 'Pending'],
                           'description': '상태 필터 (선택)'},
                'limit': {'type': 'integer', 'description': '최대 결과 수 (기본 10, 최대 20)'},
            },
        },
    },
    'get_case_detail': {
        'name': 'get_case_detail',
        'description': (
            '케이스 하나의 상세 정보(설명, 조치 이력, 해결 내용, 이메일 타임라인, '
            '연관 케이스)를 조회한다. case_ref는 C-1122 / 1122 형식 모두 허용.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'case_ref': {'type': 'string', 'description': '케이스 번호 (예: C-1122)'},
            },
            'required': ['case_ref'],
        },
    },
    'get_case_stats': {
        'name': 'get_case_stats',
        'description': '벤더별/상태별 케이스 건수와 최근 N일 신규·업데이트 건수를 집계한다.',
        'input_schema': {
            'type': 'object',
            'properties': {
                'days': {'type': 'integer', 'description': '최근 며칠 기준 (기본 30)'},
            },
        },
    },
    'list_recent_cases': {
        'name': 'list_recent_cases',
        'description': (
            '최근 N일 안에 생성되거나 업데이트된 케이스 목록을 최신순으로 반환한다. '
            '리포트 작성 시 주요 케이스 파악용.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'days': {'type': 'integer', 'description': '최근 며칠 기준 (기본 30)'},
                'vendor': {'type': 'string', 'enum': ['A10', 'Arista', 'HPE Aruba', 'Juniper'],
                           'description': '벤더 필터 (선택)'},
                'status': {'type': 'string', 'enum': ['Open', 'Resolved', 'Pending'],
                           'description': '상태 필터 (선택)'},
                'limit': {'type': 'integer', 'description': '최대 결과 수 (기본 20, 최대 50)'},
            },
        },
    },
}


def _resolve_case(case_ref):
    """'C-1122' / '1122' / '122' 어떤 형식이든 Case로 변환 (없으면 None)."""
    value = str(case_ref).strip().upper().removeprefix('C-')
    try:
        number = int(value)
    except ValueError:
        return None
    case = Case.objects.filter(id=number).first()
    if case is None and number > 1000:
        case = Case.objects.filter(id=number - 1000).first()
    return case


def _case_summary_row(case):
    return {
        'case_id': case.case_id,
        'vendor': case.vendor,
        'status': case.status,
        'summary': case.summary,
        'device_model': case.device_model,
        'software_version': case.software_version,
        'updated_at': case.updated_at.strftime('%Y-%m-%d'),
    }


def _search_cases(query='', vendor='', status='', limit=10):
    limit = min(int(limit or 10), 20)
    cases = Case.objects.all()
    if vendor:
        cases = cases.filter(vendor=vendor)
    if status:
        cases = cases.filter(status=status)

    direct = _resolve_case(query) if query else None
    if direct:
        return json.dumps({'results': [_case_summary_row(direct)]}, ensure_ascii=False)

    for keyword in (query or '').split():
        cases = cases.filter(
            Q(summary__icontains=keyword)
            | Q(description__icontains=keyword)
            | Q(action_steps__icontains=keyword)
            | Q(resolution__icontains=keyword)
            | Q(vendor_case_number__icontains=keyword)
            | Q(device_model__icontains=keyword)
            | Q(device_serial__icontains=keyword)
            | Q(software_version__icontains=keyword)
        )
    rows = [_case_summary_row(c) for c in cases.order_by('-updated_at')[:limit]]
    return json.dumps({'results': rows, 'count': len(rows)}, ensure_ascii=False)


def _get_case_detail(case_ref):
    case = _resolve_case(case_ref)
    if case is None:
        return json.dumps({'error': f'케이스를 찾을 수 없음: {case_ref}'}, ensure_ascii=False)

    emails = [
        {
            'date': e.received_at.strftime('%Y-%m-%d %H:%M'),
            'direction': e.direction,
            'subject': e.subject_ko or e.subject,
        }
        for e in case.emails.all()[:20]
    ]
    detail = _case_summary_row(case)
    detail.update({
        'vendor_case_number': case.vendor_case_number or '',
        'device_serial': case.device_serial,
        'description': (case.description or '')[:1500],
        'action_steps': (case.action_steps or '')[-2500:],  # 최근 조치 위주
        'resolution': (case.resolution or '')[:1500],
        'related_cases': [c.case_id for c in case.related_cases.all()],
        'emails': emails,
        'created_at': case.created_at.strftime('%Y-%m-%d'),
    })
    return json.dumps(detail, ensure_ascii=False)


def _get_case_stats(days=30):
    days = int(days or 30)
    since = timezone.now() - timedelta(days=days)
    by_vendor = dict(Case.objects.values_list('vendor').annotate(n=Count('id')))
    by_status = dict(Case.objects.values_list('status').annotate(n=Count('id')))
    return json.dumps({
        'total': Case.objects.count(),
        'by_vendor': by_vendor,
        'by_status': by_status,
        f'created_last_{days}d': Case.objects.filter(created_at__gte=since).count(),
        f'updated_last_{days}d': Case.objects.filter(updated_at__gte=since).count(),
    }, ensure_ascii=False)


def _list_recent_cases(days=30, vendor='', status='', limit=20):
    days = int(days or 30)
    limit = min(int(limit or 20), 50)
    since = timezone.now() - timedelta(days=days)
    cases = Case.objects.filter(Q(created_at__gte=since) | Q(updated_at__gte=since))
    if vendor:
        cases = cases.filter(vendor=vendor)
    if status:
        cases = cases.filter(status=status)

    rows = []
    for case in cases.order_by('-updated_at')[:limit]:
        row = _case_summary_row(case)
        row['is_new'] = case.created_at >= since  # 기간 내 신규 여부
        rows.append(row)
    return json.dumps({'days': days, 'results': rows, 'count': len(rows)},
                      ensure_ascii=False)


TOOL_HANDLERS = {
    'search_cases': _search_cases,
    'get_case_detail': _get_case_detail,
    'get_case_stats': _get_case_stats,
    'list_recent_cases': _list_recent_cases,
}


def _execute_tool(name, tool_input):
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return f'알 수 없는 도구: {name}', True
    try:
        return handler(**tool_input), False
    except Exception:
        logger.exception('help agent tool %s failed (input=%s)', name, tool_input)
        return '도구 실행 중 오류가 발생했습니다.', True


RE_CASE_REF = re.compile(r'\bC-(\d{3,6})\b')


def _verify_case_refs(reply):
    """응답에 인용된 C-번호를 DB와 대조 — LLM 평가자 대신 쓰는 코드 검증.

    존재하지 않는 번호가 인용됐으면(할루시네이션) 경고를 덧붙인다.
    """
    cited = {int(n) for n in RE_CASE_REF.findall(reply)}
    if not cited:
        return reply
    existing = set(
        Case.objects.filter(id__in=[n - 1000 for n in cited if n > 1000])
        .values_list('id', flat=True)
    )
    invalid = sorted(n for n in cited if n <= 1000 or (n - 1000) not in existing)
    if invalid:
        refs = ', '.join(f'C-{n}' for n in invalid)
        reply += f'\n\n⚠️ 다음 케이스 번호는 DB에서 확인되지 않았습니다: {refs}'
    return reply


def _agent_configs():
    """역할별 모델·프롬프트·도구 구성. settings는 런타임에 읽는다 (테스트 오버라이드)."""
    def tools(*names):
        return [_SEARCH_TOOL_DEFS[n] for n in names]

    return {
        'search': {
            'model': settings.HELP_AGENT_MODEL,
            'system': SEARCH_SYSTEM_PROMPT,
            'tools': tools('search_cases', 'get_case_detail', 'get_case_stats'),
        },
        'report': {
            'model': settings.REPORT_AGENT_MODEL,
            'system': REPORT_SYSTEM_PROMPT,
            'tools': tools('get_case_stats', 'list_recent_cases',
                           'search_cases', 'get_case_detail'),
            # 리포트는 표·문단이 길어 출력 여유 필요
            'max_tokens': 8000,
        },
    }


def _triage(client, messages):
    """질문을 담당 에이전트로 분류. 명백한 패턴은 규칙, 애매하면 haiku 1회 호출.

    분류 실패 시 search로 폴백 — 잘못 가도 검색 에이전트가 통계 도구를
    갖고 있어 최소한의 답은 가능하다.
    """
    question = messages[-1]['content']
    lowered = question.lower()
    if any(keyword in lowered for keyword in REPORT_KEYWORDS):
        return 'report'

    try:
        response = client.messages.create(
            model=settings.HELP_AGENT_MODEL,
            max_tokens=10,
            system=TRIAGE_SYSTEM_PROMPT,
            messages=[{'role': 'user', 'content': question[:1000]}],
        )
        text = ''.join(b.text for b in response.content if b.type == 'text').lower()
        return 'report' if 'report' in text else 'search'
    except anthropic.APIError:
        logger.warning('help agent triage failed; defaulting to search', exc_info=True)
        return 'search'


def chat(messages):
    """user/assistant 텍스트 턴 목록을 받아 트리아지 후 에이전트 루프를 실행.

    반환: {'reply', 'tool_calls': [{'name', 'input'}], 'model', 'agent'}
    API 오류는 anthropic 예외 그대로 전파 — 뷰에서 상태코드로 변환한다.
    """
    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError('ANTHROPIC_API_KEY가 설정되지 않았습니다.')

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    agent = _triage(client, messages)
    config = _agent_configs()[agent]

    convo = list(messages[-MAX_HISTORY_MESSAGES:])
    tool_trace = []

    response = None
    for _ in range(MAX_TOOL_ITERATIONS):
        response = client.messages.create(
            model=config['model'],
            max_tokens=config.get('max_tokens', 4096),
            system=config['system'],
            tools=config['tools'],
            messages=convo,
        )
        if response.stop_reason != 'tool_use':
            break

        convo.append({'role': 'assistant', 'content': response.content})
        results = []
        for block in response.content:
            if block.type != 'tool_use':
                continue
            output, is_error = _execute_tool(block.name, block.input)
            tool_trace.append({'name': block.name, 'input': block.input})
            results.append({
                'type': 'tool_result',
                'tool_use_id': block.id,
                'content': output,
                'is_error': is_error,
            })
        convo.append({'role': 'user', 'content': results})

    reply = ''.join(b.text for b in response.content if b.type == 'text').strip()
    if not reply:
        reply = '답변을 생성하지 못했습니다. 질문을 조금 더 구체적으로 해주세요.'
    return {
        'reply': _verify_case_refs(reply),
        'tool_calls': tool_trace,
        'model': config['model'],
        'agent': agent,
    }
