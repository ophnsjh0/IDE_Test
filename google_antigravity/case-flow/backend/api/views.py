import logging
import re
from datetime import timedelta
from urllib.parse import quote

import anthropic
from django.db import transaction
from django.db.models import Count, F, Max, Q
from django.http import HttpResponse
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from django.conf import settings

from .models import AppSetting, Case, ChatSession, ChatTurn, KnowledgeItem, UsageEvent
from .permissions import IsAdminRole, IsEngineerOrAbove
from .serializers import (CaseSerializer, CaseDetailSerializer,
                          ChatSessionDetailSerializer, ChatSessionSerializer,
                          KnowledgeItemSerializer)
from .services.usage import log_event
from .services.analyzer import (
    AVAILABLE_MODELS,
    TRANSLATION_MODEL_SETTING_KEY,
    detect_provider,
    get_translation_model,
    provider_api_key,
)
from .services import help_agent
from .services.gmail_client import GmailAuthError
from .services.gmail_sync import SyncInProgress, sync_gmail

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    return Response({"status": "ok", "message": "Backend is running!"})


# Cases created from email are dated by mail activity, not by when the
# sync ran; manual cases fall back to created_at.
CASES_WITH_LAST_EMAIL = Case.objects.annotate(
    last_email_at=Max('emails__received_at'),
)


class CaseListCreateView(generics.ListCreateAPIView):
    queryset = CASES_WITH_LAST_EMAIL.order_by(
        Coalesce('last_email_at', 'created_at').desc()
    )
    serializer_class = CaseSerializer

    def get_permissions(self):
        # 조회는 전 역할, 생성은 엔지니어 이상
        if self.request.method == 'POST':
            return [IsEngineerOrAbove()]
        return super().get_permissions()

    def get(self, request, *args, **kwargs):
        log_event(request.user, 'case_list')
        return super().get(request, *args, **kwargs)


class CaseDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = CASES_WITH_LAST_EMAIL
    serializer_class = CaseDetailSerializer
    lookup_field = 'id'

    def get_permissions(self):
        # 조회는 전 역할, 수정은 엔지니어 이상, 삭제는 관리자만
        if self.request.method == 'DELETE':
            return [IsAdminRole()]
        if self.request.method in ('PUT', 'PATCH'):
            return [IsEngineerOrAbove()]
        return super().get_permissions()

    def get(self, request, *args, **kwargs):
        log_event(request.user, 'case_view', detail=f"C-{1000 + kwargs['id']}")
        return super().get(request, *args, **kwargs)


def _resolve_case_ref(ref):
    """'C-1118', '1118'(표시 번호) 또는 DB id 문자열을 Case로 변환."""
    ref = str(ref or '').strip().upper()
    if ref.startswith('C-'):
        ref = ref[2:]
    if not ref.isdigit():
        return None
    number = int(ref)
    if number > 1000:  # 표시 번호(C-{1000+id}) -> DB id
        number -= 1000
    return Case.objects.filter(id=number).first()


class CaseRelationView(APIView):
    """케이스 간 상호 참조 관리 (엔지니어 이상).

    POST   /api/cases/<id>/relations/          {case_id: "C-1118"}  — 참조 추가
    DELETE /api/cases/<id>/relations/<other>/                        — 참조 해제
    관계는 대칭(M2M symmetrical)이라 어느 쪽에서 추가/해제해도 양쪽에 반영된다.
    """
    permission_classes = [IsEngineerOrAbove]

    def post(self, request, id):
        case = Case.objects.filter(id=id).first()
        if case is None:
            return Response({'error': '존재하지 않는 케이스입니다.'},
                            status=status.HTTP_404_NOT_FOUND)
        other = _resolve_case_ref(request.data.get('case_id'))
        if other is None:
            return Response({'error': '케이스를 찾을 수 없습니다. C-1118 형식으로 입력하세요.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if other == case:
            return Response({'error': '자기 자신은 참조로 추가할 수 없습니다.'},
                            status=status.HTTP_400_BAD_REQUEST)
        case.related_cases.add(other)
        return Response({'message': f'{other.case_id} 참조가 추가되었습니다.'},
                        status=status.HTTP_201_CREATED)

    def delete(self, request, id, other_id):
        case = Case.objects.filter(id=id).first()
        other = Case.objects.filter(id=other_id).first()
        if case is None or other is None:
            return Response({'error': '존재하지 않는 케이스입니다.'},
                            status=status.HTTP_404_NOT_FOUND)
        case.related_cases.remove(other)
        return Response({'message': f'{other.case_id} 참조가 해제되었습니다.'})


class KnowledgeListView(generics.ListAPIView):
    """GET /api/knowledge/ — 지식 베이스 목록 (전 역할 조회).

    항목 생성은 extract_knowledge 커맨드(AI 추출)로만 이루어진다.
    """
    queryset = KnowledgeItem.objects.select_related('case')
    serializer_class = KnowledgeItemSerializer

    def get(self, request, *args, **kwargs):
        log_event(request.user, 'knowledge_view', detail='list')
        return super().get(request, *args, **kwargs)


class KnowledgeDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = KnowledgeItem.objects.select_related('case')
    serializer_class = KnowledgeItemSerializer
    lookup_field = 'id'

    def get_permissions(self):
        # 케이스와 동일한 규칙: 조회 전 역할, 수정(확정 포함) 엔지니어 이상, 삭제 관리자
        if self.request.method == 'DELETE':
            return [IsAdminRole()]
        if self.request.method in ('PUT', 'PATCH'):
            return [IsEngineerOrAbove()]
        return super().get_permissions()

    def get(self, request, *args, **kwargs):
        log_event(request.user, 'knowledge_view', detail=f"K-{100 + kwargs['id']}")
        return super().get(request, *args, **kwargs)


class TranslationModelView(APIView):
    """GET/PUT /api/settings/translation-model/ — AI 분석 모델 조회/변경.

    프론트에서 선택한 모델은 AppSetting(DB)에 저장되어 서버 재시작 후에도 유지되며,
    settings.py의 기본값보다 우선한다. {"model": "default"}를 보내면 기본값으로 복귀.
    """

    def get_permissions(self):
        # 모델 변경은 비용에 영향 -> 관리자만. 조회는 전 역할.
        if self.request.method == 'PUT':
            return [IsAdminRole()]
        return super().get_permissions()

    def get(self, request):
        return Response(self._payload())

    def put(self, request):
        model = (request.data.get('model') or '').strip()

        if model == 'default':
            AppSetting.objects.filter(key=TRANSLATION_MODEL_SETTING_KEY).delete()
            return Response(self._payload())

        if model not in {m['id'] for m in AVAILABLE_MODELS}:
            return Response({'error': f'지원하지 않는 모델입니다: {model}'},
                            status=status.HTTP_400_BAD_REQUEST)
        if not provider_api_key(detect_provider(model)):
            return Response({'error': '해당 제공자의 API 키가 .env에 설정되어 있지 않습니다.'},
                            status=status.HTTP_400_BAD_REQUEST)

        AppSetting.set(TRANSLATION_MODEL_SETTING_KEY, model)
        return Response(self._payload())

    @staticmethod
    def _payload():
        return {
            'current': get_translation_model(),
            'default': settings.TRANSLATION_MODEL,
            'models': [
                {**m, 'key_configured': bool(provider_api_key(m['provider']))}
                for m in AVAILABLE_MODELS
            ],
        }


class DashboardStatsView(APIView):
    """GET /api/dashboard/stats/?days=N — 벤더별 상태/최근 활동 집계.

    recent_created: 최근 N일 내 생성된 케이스 수.
    recent_updated: 그 전에 생성됐지만 최근 N일 내 갱신된 케이스 수
    (신규와 중복 집계되지 않도록 분리).
    """

    def get(self, request):
        try:
            days = int(request.query_params.get('days', 7))
        except (TypeError, ValueError):
            days = 7
        days = max(1, min(days, 365))
        cutoff = timezone.now() - timedelta(days=days)

        rows = Case.objects.values('vendor').annotate(
            total=Count('id'),
            open=Count('id', filter=Q(status='Open')),
            pending=Count('id', filter=Q(status='Pending')),
            resolved=Count('id', filter=Q(status='Resolved')),
            recent_created=Count('id', filter=Q(created_at__gte=cutoff)),
            recent_updated=Count('id', filter=Q(updated_at__gte=cutoff,
                                                created_at__lt=cutoff)),
        )
        by_vendor = {row['vendor']: row for row in rows}

        fields = ('total', 'open', 'pending', 'resolved',
                  'recent_created', 'recent_updated')
        empty = dict.fromkeys(fields, 0)
        vendors = [
            {'vendor': vendor, **{f: by_vendor.get(vendor, empty)[f] for f in fields}}
            for vendor, _ in Case.VENDOR_CHOICES
        ]
        totals = {f: sum(v[f] for v in vendors) for f in fields}

        return Response({'days': days, 'vendors': vendors, 'totals': totals})


class GmailSyncView(APIView):
    """POST /api/gmail/sync/ — pull vendor case mail from Gmail into Case-Flow."""
    permission_classes = [IsEngineerOrAbove]

    def post(self, request):
        try:
            summary = sync_gmail()
        except SyncInProgress as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        except GmailAuthError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception("Gmail sync failed")
            return Response(
                {'error': 'Gmail 동기화 중 오류가 발생했습니다. 서버 로그를 확인하세요.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        log_event(request.user, 'gmail_sync',
                  detail=f"fetched={summary.get('fetched')} created={summary.get('cases_created')}")
        return Response(summary)


class HelpAgentChatView(APIView):
    """POST /api/help-agent/chat/ — 케이스 DB 검색 헬프 에이전트와 대화.

    엔지니어 이상 사용 가능 (2026-07-21, 관리자 전용에서 확대).
    본문: {"messages": [{"role", "content"}, ...], "session_id": 123(선택)}
    대화는 ChatSession/ChatTurn으로 저장된다 — session_id가 오면 그 세션에
    마지막 질문·답변 턴만 추가하고(이전 턴은 이미 저장돼 있음), 없으면
    새 세션을 만든다. 응답에 session_id를 돌려줘 프론트가 이어가게 한다.
    """

    permission_classes = [IsEngineerOrAbove]

    MAX_CONTENT_LENGTH = 4000

    def post(self, request):
        messages = request.data.get('messages')
        error = self._validate(messages)
        if error:
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

        session = None
        session_id = request.data.get('session_id')
        if session_id is not None:
            session = ChatSession.objects.filter(
                id=session_id, user=request.user).first()
            if session is None:
                return Response({'error': '세션을 찾을 수 없습니다.'},
                                status=status.HTTP_404_NOT_FOUND)

        try:
            result = help_agent.chat(messages)
        except anthropic.RateLimitError:
            return Response(
                {'error': 'AI 사용량 한도에 걸렸습니다. 잠시 후 다시 시도해주세요.'},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        except anthropic.APIStatusError as e:
            logger.exception("help agent API error (%s)", e.status_code)
            return Response(
                {'error': 'AI 서비스 오류가 발생했습니다. 잠시 후 다시 시도해주세요.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except (anthropic.APIConnectionError, RuntimeError):
            logger.exception("help agent unavailable")
            return Response(
                {'error': 'AI 서비스에 연결할 수 없습니다. 서버 설정을 확인하세요.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        # 어떤 에이전트가 어떤 질문을 받았는지 파일럿 지표로 남긴다 (질문은 앞 80자만)
        log_event(request.user, 'agent_chat',
                  detail=f"[{result.get('agent', '?')}] {messages[-1]['content'][:80]}")
        result['session_id'] = self._save_turns(
            request.user, session, messages[-1]['content'], result)
        return Response(result)

    @staticmethod
    def _save_turns(user, session, question, result):
        """질문·답변 턴을 세션에 저장하고 세션 id를 반환.

        저장은 부가 기능 — 이미 비용이 발생한 답변을 저장 실패로 잃지
        않도록 예외를 전파하지 않는다 (session_id: null로 응답).
        """
        try:
            with transaction.atomic():
                if session is None:
                    session = ChatSession.objects.create(
                        user=user, title=question[:200])
                ChatTurn.objects.create(
                    session=session, role='user', content=question)
                ChatTurn.objects.create(
                    session=session, role='assistant',
                    content=result.get('reply', ''),
                    agent=result.get('agent', ''),
                    model=result.get('model', ''),
                    tool_calls=result.get('tool_calls', []),
                    files=result.get('files', []),
                )
                session.save(update_fields=['updated_at'])
            return session.id
        except Exception:
            logger.exception("failed to persist chat session")
            return None

    def _validate(self, messages):
        if not isinstance(messages, list) or not messages:
            return 'messages 목록이 필요합니다.'
        for m in messages:
            if (not isinstance(m, dict)
                    or m.get('role') not in ('user', 'assistant')
                    or not isinstance(m.get('content'), str)
                    or not m['content'].strip()):
                return '각 메시지는 {role: user|assistant, content: 문자열} 형식이어야 합니다.'
            if len(m['content']) > self.MAX_CONTENT_LENGTH:
                return f'메시지는 {self.MAX_CONTENT_LENGTH}자를 넘을 수 없습니다.'
        if messages[-1]['role'] != 'user':
            return '마지막 메시지는 사용자 질문이어야 합니다.'
        return None


class ChatSessionListView(generics.ListAPIView):
    """GET /api/help-agent/sessions/ — 내 대화 세션 목록 (최근 갱신순).

    대화 원문은 본인만 접근 (질문을 남이 본다는 부담이 사용을 위축시키지
    않도록). 지식 추출 2단계에서 정제된 지식만 전체 공유될 예정.
    """

    permission_classes = [IsEngineerOrAbove]
    serializer_class = ChatSessionSerializer

    def get_queryset(self):
        return ChatSession.objects.filter(user=self.request.user)


class ChatSessionDetailView(APIView):
    """GET/DELETE /api/help-agent/sessions/<id>/ — 세션 대화 내용 조회/삭제 (본인만)."""

    permission_classes = [IsEngineerOrAbove]

    def _get_session(self, request, session_id):
        return ChatSession.objects.filter(id=session_id, user=request.user).first()

    def get(self, request, session_id):
        session = self._get_session(request, session_id)
        if session is None:
            return Response({'error': '세션을 찾을 수 없습니다.'},
                            status=status.HTTP_404_NOT_FOUND)
        return Response(ChatSessionDetailSerializer(session).data)

    def delete(self, request, session_id):
        session = self._get_session(request, session_id)
        if session is None:
            return Response({'error': '세션을 찾을 수 없습니다.'},
                            status=status.HTTP_404_NOT_FOUND)
        session.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ChatKnowledgeExtractView(APIView):
    """POST /api/help-agent/sessions/<id>/knowledge/ — 대화에서 지식 추출.

    사용자가 대화가 유효한 결론에 도달했다고 판단했을 때 명시적으로 호출
    ("이 대화를 지식으로 저장" 버튼). AI가 시행착오를 걸러 문제-원인-해결을
    정제해 KnowledgeItem(draft, 출처=chat_session)으로 저장한다.
    본인 세션만 가능. 이미 추출된 세션이면 기존 항목을 돌려준다.
    """

    permission_classes = [IsEngineerOrAbove]

    ERROR_MESSAGES = {
        'no_knowledge': '이 대화에서는 재사용할 만한 문제-해결 지식을 찾지 못했습니다. '
                        '해결책이 오간 대화에서 다시 시도해주세요.',
        'no_vendor': '대화에서 어느 벤더(A10/Arista/HPE Aruba/Juniper) 장비인지 알 수 '
                     '없어 지식으로 저장하지 못했습니다. 벤더나 장비 모델을 언급한 뒤 '
                     '다시 시도해주세요.',
        'failed': 'AI 추출에 실패했습니다. 잠시 후 다시 시도해주세요.',
    }

    def post(self, request, session_id):
        session = ChatSession.objects.filter(id=session_id, user=request.user).first()
        if session is None:
            return Response({'error': '세션을 찾을 수 없습니다.'},
                            status=status.HTTP_404_NOT_FOUND)

        from .services.knowledge import extract_knowledge_from_chat
        try:
            outcome, item = extract_knowledge_from_chat(session)
        except Exception:
            logger.exception("chat knowledge extraction failed (session %s)", session_id)
            return Response({'error': self.ERROR_MESSAGES['failed']},
                            status=status.HTTP_502_BAD_GATEWAY)

        if outcome in self.ERROR_MESSAGES:
            return Response({'error': self.ERROR_MESSAGES[outcome], 'outcome': outcome},
                            status=status.HTTP_502_BAD_GATEWAY
                            if outcome == 'failed' else status.HTTP_400_BAD_REQUEST)

        log_event(request.user, 'knowledge_extract',
                  detail=f"session={session_id} -> {item.knowledge_id} ({outcome})")
        return Response({'outcome': outcome,
                         'item': KnowledgeItemSerializer(item).data},
                        status=status.HTTP_201_CREATED if outcome == 'created'
                        else status.HTTP_200_OK)


RE_ANTHROPIC_FILE_ID = re.compile(r'^file_[A-Za-z0-9_-]+$')


class HelpAgentFileView(APIView):
    """GET /api/help-agent/files/<file_id>/ — 리포팅 에이전트가 생성한
    문서(워드/엑셀/PPT)를 Anthropic Files API에서 받아 다운로드로 중계.
    채팅과 동일하게 엔지니어 이상.
    """

    permission_classes = [IsEngineerOrAbove]

    def get(self, request, file_id):
        if not RE_ANTHROPIC_FILE_ID.match(file_id):
            return Response({'error': '잘못된 파일 ID입니다.'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            filename, mime_type, data = help_agent.download_file(file_id)
        except anthropic.NotFoundError:
            return Response({'error': '파일을 찾을 수 없습니다. 생성 후 시간이 지나 만료되었을 수 있습니다.'},
                            status=status.HTTP_404_NOT_FOUND)
        except (anthropic.APIError, RuntimeError):
            logger.exception("help agent file download failed (%s)", file_id)
            return Response({'error': '파일 다운로드 중 오류가 발생했습니다.'},
                            status=status.HTTP_502_BAD_GATEWAY,)

        log_event(request.user, 'report_download', detail=filename)
        response = HttpResponse(
            data, content_type=mime_type or 'application/octet-stream')
        # 파일명에 한글 등 비ASCII가 올 수 있어 RFC 5987 형식으로 지정
        response['Content-Disposition'] = (
            f"attachment; filename*=UTF-8''{quote(filename)}")
        return response


class UsageEventView(APIView):
    """POST /api/usage/ — 서버가 볼 수 없는 프론트 이벤트(클라이언트 검색 등) 기록.

    허용 목록에 있는 이벤트만 받는다 — 임의 이벤트로 지표가 오염되는 것 방지.
    """

    CLIENT_EVENTS = {'search'}

    def post(self, request):
        event = request.data.get('event')
        if event not in self.CLIENT_EVENTS:
            return Response({'error': '허용되지 않은 이벤트입니다.'},
                            status=status.HTTP_400_BAD_REQUEST)
        log_event(request.user, event, detail=str(request.data.get('detail') or ''))
        return Response({'ok': True}, status=status.HTTP_201_CREATED)


class UsageStatsView(APIView):
    """GET /api/usage/stats/?days=28 — 파일럿 지표 요약 (admin 전용).

    반환: 기간 내 활성 사용자 수, 이벤트 유형별 건수, 일별 활성 사용자,
    사용자별 요약(마지막 활동·검색/채팅/케이스 조회 횟수).
    """

    permission_classes = [IsAdminRole]

    def get(self, request):
        try:
            days = min(max(int(request.query_params.get('days', 28)), 1), 90)
        except ValueError:
            days = 28
        since = timezone.now() - timedelta(days=days)
        qs = UsageEvent.objects.filter(created_at__gte=since)

        by_event = {
            row['event']: row['n']
            for row in qs.values('event').annotate(n=Count('id'))
        }
        daily = list(
            qs.exclude(user=None)
            .annotate(day=TruncDate('created_at'))
            .values('day')
            .annotate(users=Count('user', distinct=True), events=Count('id'))
            .order_by('day')
        )
        users = list(
            qs.exclude(user=None)
            .values(username=F('user__username'))
            .annotate(
                events=Count('id'),
                logins=Count('id', filter=Q(event='login')),
                case_views=Count('id', filter=Q(event='case_view')),
                searches=Count('id', filter=Q(event='search')),
                agent_chats=Count('id', filter=Q(event='agent_chat')),
                last_active=Max('created_at'),
            )
            .order_by('-events')
        )
        return Response({
            'days': days,
            'active_users': qs.exclude(user=None).values('user').distinct().count(),
            'by_event': by_event,
            'daily': daily,
            'users': users,
        })
