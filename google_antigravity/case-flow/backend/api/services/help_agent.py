"""헬프 에이전트 ① — DB 검색.

사용자가 케이스 조회·유사 사례 검색을 자연어로 질문하면, Claude가
케이스 DB 조회 도구를 호출해 근거를 확보한 뒤 답한다. 외부 지식·웹 검색은
이 에이전트의 범위가 아니다 (추후 별도 에이전트).

비용/역할 설계(2026-07-11 합의): DB 검색은 저비용 모델(haiku)로 충분.
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

SYSTEM_PROMPT = """당신은 Case-Flow(벤더 TAC 케이스 관리 시스템)의 도우미입니다.
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

TOOLS = [
    {
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
    {
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
    {
        'name': 'get_case_stats',
        'description': '벤더별/상태별 케이스 건수와 최근 N일 신규·업데이트 건수를 집계한다.',
        'input_schema': {
            'type': 'object',
            'properties': {
                'days': {'type': 'integer', 'description': '최근 며칠 기준 (기본 30)'},
            },
        },
    },
]


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


TOOL_HANDLERS = {
    'search_cases': _search_cases,
    'get_case_detail': _get_case_detail,
    'get_case_stats': _get_case_stats,
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


def chat(messages):
    """user/assistant 텍스트 턴 목록을 받아 에이전트 루프를 실행.

    반환: {'reply': str, 'tool_calls': [{'name', 'input'}], 'model': str}
    API 오류는 anthropic 예외 그대로 전파 — 뷰에서 상태코드로 변환한다.
    """
    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError('ANTHROPIC_API_KEY가 설정되지 않았습니다.')

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    model = settings.HELP_AGENT_MODEL
    convo = list(messages[-MAX_HISTORY_MESSAGES:])
    tool_trace = []

    response = None
    for _ in range(MAX_TOOL_ITERATIONS):
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
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
    return {'reply': _verify_case_refs(reply), 'tool_calls': tool_trace, 'model': model}
