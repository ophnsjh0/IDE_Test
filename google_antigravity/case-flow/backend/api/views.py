import json
import logging
import queue
import re
import threading
from datetime import timedelta
from urllib.parse import quote

import anthropic
from django.db import connection, transaction
from django.db.models import Count, F, Max, Q
from django.http import HttpResponse, StreamingHttpResponse
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
                          KnowledgeItemSerializer, LabDetailSerializer,
                          LabSerializer)
from .services.usage import log_event
from .services.analyzer import (
    AVAILABLE_MODELS,
    KNOWLEDGE_MODEL_DEFAULT,
    KNOWLEDGE_MODEL_SETTING_KEY,
    KNOWLEDGE_MODELS,
    TRANSLATION_MODEL_SETTING_KEY,
    detect_provider,
    get_knowledge_model,
    get_translation_model,
    provider_api_key,
)
from .services import eveng, help_agent
from .services.gmail_client import GmailAuthError
from .services.gmail_sync import (LAST_RUN_SETTING_KEY, SyncInProgress,
                                  is_cron_enabled, set_cron_enabled, sync_gmail)

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


class KnowledgeModelView(APIView):
    """GET/PUT /api/settings/knowledge-model/ — 지식 추출 전용 모델 조회/변경.

    메일 분석 모델(translation-model)과 분리돼 있다. 메일은 건수가 많아 저비용
    모델이 합리적이지만 지식은 케이스당 1회 만들어 오래 재사용하는 자산이라
    품질이 우선이고, 그래서 선택지도 상위 두 모델(analyzer.KNOWLEDGE_MODELS)로
    묶여 있다. 기본값은 Opus 5.
    """

    def get_permissions(self):
        # 비용에 영향 -> 변경은 관리자만. 조회는 전 역할(어떤 모델로 뽑혔는지 표시).
        if self.request.method == 'PUT':
            return [IsAdminRole()]
        return super().get_permissions()

    def get(self, request):
        return Response(self._payload())

    def put(self, request):
        model = (request.data.get('model') or '').strip()
        if model not in KNOWLEDGE_MODELS:
            allowed = ', '.join(KNOWLEDGE_MODELS)
            return Response({'error': f'지식 추출 모델은 {allowed} 중에서만 선택할 수 있습니다.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if not provider_api_key(detect_provider(model)):
            return Response({'error': '해당 제공자의 API 키가 .env에 설정되어 있지 않습니다.'},
                            status=status.HTTP_400_BAD_REQUEST)

        AppSetting.set(KNOWLEDGE_MODEL_SETTING_KEY, model)
        return Response(self._payload())

    @staticmethod
    def _payload():
        catalog = {m['id']: m for m in AVAILABLE_MODELS}
        return {
            'current': get_knowledge_model(),
            'default': KNOWLEDGE_MODEL_DEFAULT,
            'models': [
                {**catalog.get(model_id, {'id': model_id, 'provider': 'anthropic', 'note': ''}),
                 'key_configured': bool(provider_api_key(detect_provider(model_id)))}
                for model_id in KNOWLEDGE_MODELS
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


class GmailSyncScheduleView(APIView):
    """GET/PUT /api/settings/gmail-sync/ — cron 자동 수집 스위치.

    VM의 crontab은 계속 돌지만, 이 스위치가 꺼져 있으면 수집을 건너뛴다
    (컨테이너에서 호스트 crontab을 직접 못 건드리므로 DB 값으로 제어).
    웹의 수동 동기화 버튼은 이 스위치와 무관하게 동작한다.
    """

    def get_permissions(self):
        # 조회는 전 역할(상태 표시), 변경은 관리자만
        if self.request.method == 'PUT':
            return [IsAdminRole()]
        return super().get_permissions()

    def get(self, request):
        return Response(self._payload())

    def put(self, request):
        enabled = request.data.get('enabled')
        if not isinstance(enabled, bool):
            return Response({'error': 'enabled는 true/false여야 합니다.'},
                            status=status.HTTP_400_BAD_REQUEST)
        set_cron_enabled(enabled)
        log_event(request.user, 'gmail_cron_toggle', detail='on' if enabled else 'off')
        return Response(self._payload())

    @staticmethod
    def _payload():
        return {
            'enabled': is_cron_enabled(),
            'last_run': AppSetting.get(LAST_RUN_SETTING_KEY),
            'schedule': settings.GMAIL_SYNC_SCHEDULE_LABEL,
        }


class HelpAgentChatView(APIView):
    """POST /api/help-agent/chat/ — 케이스 DB 검색 헬프 에이전트와 대화.

    엔지니어 이상 사용 가능 (2026-07-21, 관리자 전용에서 확대).
    본문: {"messages": [{"role", "content"}, ...], "session_id": 123(선택)}
    대화는 ChatSession/ChatTurn으로 저장된다 — session_id가 오면 그 세션에
    마지막 질문·답변 턴만 추가하고(이전 턴은 이미 저장돼 있음), 없으면
    새 세션을 만든다. 응답에 session_id를 돌려줘 프론트가 이어가게 한다.
    """

    permission_classes = [IsEngineerOrAbove]

    MAX_CONTENT_LENGTH = 20000
    MAX_ATTACHMENTS = 5

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
        question = help_agent.question_text(messages[-1])
        log_event(request.user, 'agent_chat',
                  detail=f"[{result.get('agent', '?')}] {question[:80]}")
        result['session_id'] = self._save_turns(
            request.user, session, messages[-1], result)
        return Response(result)

    @staticmethod
    def _save_turns(user, session, message, result):
        """질문·답변 턴을 세션에 저장하고 세션 id를 반환.

        저장은 부가 기능 — 이미 비용이 발생한 답변을 저장 실패로 잃지
        않도록 예외를 전파하지 않는다 (session_id: null로 응답).
        """
        question = help_agent.question_text(message)
        try:
            with transaction.atomic():
                if session is None:
                    session = ChatSession.objects.create(
                        user=user, title=question[:200])
                ChatTurn.objects.create(
                    session=session, role='user', content=message['content'],
                    attachments=message.get('attachments') or [])
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
                    or not isinstance(m.get('content'), str)):
                return '각 메시지는 {role: user|assistant, content: 문자열} 형식이어야 합니다.'
            if len(m['content']) > self.MAX_CONTENT_LENGTH:
                return f'메시지는 {self.MAX_CONTENT_LENGTH}자를 넘을 수 없습니다.'
            error = self._validate_attachments(m)
            if error:
                return error
            # 첨부만 올리고 본문을 비우는 사용(스크린샷 한 장)은 허용한다
            if not m['content'].strip() and not m.get('attachments'):
                return '빈 메시지는 보낼 수 없습니다.'
        if messages[-1]['role'] != 'user':
            return '마지막 메시지는 사용자 질문이어야 합니다.'
        return None

    def _validate_attachments(self, message):
        """첨부 메타 형식 검사. 실제 파일은 업로드 시점에 이미 검증됐고,
        여기서는 모델에 넘길 수 있는 모양인지만 본다."""
        attachments = message.get('attachments')
        if attachments is None:
            return None
        if not isinstance(attachments, list) or len(attachments) > self.MAX_ATTACHMENTS:
            return f'첨부는 메시지당 {self.MAX_ATTACHMENTS}개까지 목록으로 보낼 수 있습니다.'
        for a in attachments:
            if (not isinstance(a, dict)
                    or not isinstance(a.get('file_id'), str) or not a['file_id']
                    or a.get('kind') not in ('image', 'document')):
                return '첨부 형식이 올바르지 않습니다. 파일을 다시 첨부해주세요.'
        if message.get('role') != 'user' and attachments:
            return '첨부는 사용자 메시지에만 붙일 수 있습니다.'
        return None


class HelpAgentChatStreamView(HelpAgentChatView):
    """POST /api/help-agent/chat/stream/ — 채팅 + 진행 상황 SSE 스트리밍.

    기존 /chat/과 입력·결과가 완전히 같고, 답변이 나오기까지 어떤 도구를 쓰는지
    실시간으로 흘려보낸다는 점만 다르다 (사용자가 멈춘 것으로 오해하지 않도록).

    이벤트: step(triage/thinking/evaluating/revising) · start(담당 배정) ·
            tool(도구 이름만 — 입력값은 고객사명 등이 섞일 수 있어 보내지 않는다) ·
            done(기존 /chat/ 응답 본문 그대로) · error

    설계 요점:
    - 검증(인증·형식·세션)은 첫 바이트 이전에 끝낸다. 스트림이 시작되면 상태
      코드를 바꿀 수 없으므로, 이벤트로 전달할 오류를 AI 런타임 실패로 좁힌다.
    - chat()은 동기 함수라 별도 스레드에서 돌리고 큐로 이벤트를 받는다.
      큐는 무제한이라 클라이언트가 끊겨도 작업 스레드가 막히지 않는다.
    - 대화 저장은 작업 스레드가 수행한다 — 창을 닫아도 이미 비용이 발생한
      답변은 남는다.
    """

    @staticmethod
    def _stream_error(exc):
        """예외를 (code, 사용자 문구)로 변환. 문구는 비스트리밍 뷰와 동일하게 유지한다.

        RateLimitError가 APIStatusError의 하위 클래스라 검사 순서가 중요하다.
        """
        if isinstance(exc, anthropic.RateLimitError):
            return 'rate_limit', 'AI 사용량 한도에 걸렸습니다. 잠시 후 다시 시도해주세요.'
        if isinstance(exc, anthropic.APIStatusError):
            return 'upstream', 'AI 서비스 오류가 발생했습니다. 잠시 후 다시 시도해주세요.'
        if isinstance(exc, (anthropic.APIConnectionError, RuntimeError)):
            return 'unavailable', 'AI 서비스에 연결할 수 없습니다. 서버 설정을 확인하세요.'
        return 'internal', 'AI 응답 생성 중 오류가 발생했습니다.'

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

        response = StreamingHttpResponse(
            self._stream(request.user, session, messages),
            content_type='text/event-stream')
        # 중간 프록시가 생기더라도 버퍼링되지 않도록 (현재 구성엔 프록시 없음)
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'
        return response

    def _stream(self, user, session, messages):
        events = queue.Queue()  # 무제한 — 소비자가 끊겨도 생산자가 막히지 않는다

        def worker():
            terminal = None
            try:
                result = help_agent.chat(
                    messages,
                    on_event=lambda kind, payload: events.put((kind, payload)))
                # 저장·로깅은 클라이언트 연결 여부와 무관하게 여기서 끝낸다.
                # 둘 다 내부에서 예외를 삼키므로 답변을 잃지 않는다.
                question = help_agent.question_text(messages[-1])
                log_event(user, 'agent_chat',
                          detail=f"[{result.get('agent', '?')}] {question[:80]}")
                result['session_id'] = self._save_turns(
                    user, session, messages[-1], result)
                terminal = ('done', result)
            except Exception as exc:
                code, message = self._stream_error(exc)
                logger.warning('help agent stream failed (%s)', code, exc_info=True)
                terminal = ('error', {'code': code, 'message': message})
            finally:
                # 어떤 경로로 끝나도 종료 이벤트를 정확히 하나 보낸다
                # (없으면 소비자가 큐에서 영원히 대기한다)
                events.put(terminal or ('error', {
                    'code': 'internal',
                    'message': 'AI 응답 생성 중 오류가 발생했습니다.'}))
                # 스레드마다 별도 DB 커넥션이 열리므로 반드시 닫는다
                connection.close()

        threading.Thread(target=worker, daemon=True).start()

        while True:
            kind, payload = events.get()
            yield f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if kind in ('done', 'error'):
                break


class HelpAgentAttachmentView(APIView):
    """POST /api/help-agent/attachments/ — 채팅에 붙일 파일(스크린샷·설정 파일·
    벤더 PDF)을 Anthropic Files API에 올리고 file_id를 돌려준다.

    프론트가 직접 올리지 않고 서버가 중계하는 이유: API 키가 브라우저로 나가지
    않고, 형식·크기 검증을 한 곳에서 강제할 수 있다. 반환된 file_id는 이후
    채팅 요청의 messages[].attachments에 실려 온다.
    """

    permission_classes = [IsEngineerOrAbove]

    def post(self, request):
        upload = request.FILES.get('file')
        if upload is None:
            return Response({'error': '파일이 필요합니다.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if upload.size > help_agent.MAX_ATTACHMENT_BYTES:
            limit_mb = help_agent.MAX_ATTACHMENT_BYTES // (1024 * 1024)
            return Response(
                {'error': f'파일이 너무 큽니다. {limit_mb}MB 이하만 첨부할 수 있습니다.'},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

        try:
            attachment = help_agent.upload_attachment(upload.name, upload.read())
        except help_agent.AttachmentRejected as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except (anthropic.APIError, RuntimeError):
            logger.exception("chat attachment upload failed (%s)", upload.name)
            return Response({'error': '파일 업로드 중 오류가 발생했습니다.'},
                            status=status.HTTP_502_BAD_GATEWAY)
        return Response(attachment, status=status.HTTP_201_CREATED)


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
        # 대화를 지우면 첨부 원본도 Anthropic 스토리지에서 지운다 —
        # 고객사 자료가 올라올 수 있어 참조만 지우고 남겨두면 안 된다.
        file_ids = [a['file_id']
                    for turn in session.turns.all()
                    for a in (turn.attachments or [])
                    if isinstance(a, dict) and a.get('file_id')]
        session.delete()
        if file_ids:
            try:
                help_agent.delete_files(file_ids)
            except Exception:  # 정리 실패가 삭제 응답을 막지 않게
                logger.warning("chat attachment cleanup failed (session %s)",
                               session_id, exc_info=True)
        return Response(status=status.HTTP_204_NO_CONTENT)


class KnowledgeSyncView(APIView):
    """POST /api/knowledge/sync/ — 미검토 Resolved 케이스에서 지식 일괄 추출.

    Gmail 동기화와 같은 관리자 버튼. 케이스당 AI 호출 비용이 들어
    한 번에 SYNC_MAX_CASES건까지만 처리하고 남은 건수를 돌려준다.
    """

    permission_classes = [IsAdminRole]

    def post(self, request):
        from .services.knowledge import sync_from_cases
        try:
            summary = sync_from_cases()
        except SyncInProgress as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        except Exception:
            logger.exception("knowledge sync failed")
            return Response({'error': '지식 동기화 중 오류가 발생했습니다. 서버 로그를 확인하세요.'},
                            status=status.HTTP_502_BAD_GATEWAY)
        log_event(request.user, 'knowledge_extract',
                  detail=(f"sync scanned={summary['scanned']} created={summary['created']} "
                          f"remaining={summary['remaining']}"))
        return Response(summary)


class CaseKnowledgeExtractView(APIView):
    """POST /api/cases/<id>/knowledge/ — 이 케이스에서 지식 추출 (상태 무관).

    관리자용 '지식 동기화'(Resolved 일괄)와 목적이 다르다. 벤더의 확답은
    케이스가 종결되기 한참 전에 나오는 일이 많아, Resolved만 기다리면 그동안
    정보가 묶여 있다 (2026-08-11: C-1118이 Pending인 채로 ACOS-104904 관련
    벤더 확답을 43건의 메일 속에 묶어두고 있었다). 엔지니어가 "지금 이건
    남길 가치가 있다"고 판단한 시점에 누르는 버튼.

    AI 대화 → 지식 저장 버튼과 같은 규칙: 엔지니어 이상, 케이스당 1회,
    이미 추출됐으면 기존 항목을 돌려준다.
    """

    permission_classes = [IsEngineerOrAbove]

    ERROR_MESSAGES = {
        'no_knowledge': '이 케이스에서는 재사용할 만한 지식(문제-해결, 설정 절차, '
                        '벤더 확답)을 찾지 못했습니다. 해결책이나 벤더 답변이 오간 '
                        '뒤에 다시 시도해주세요.',
        'failed': 'AI 추출에 실패했습니다. 잠시 후 다시 시도해주세요.',
    }

    def post(self, request, id):
        case = Case.objects.filter(id=id).first()
        if case is None:
            return Response({'error': '존재하지 않는 케이스입니다.'},
                            status=status.HTTP_404_NOT_FOUND)

        from .services.knowledge import extract_knowledge
        try:
            # 진행 중인 케이스를 '검토 완료'로 찍으면 나중에 해결됐을 때
            # 자동 동기화가 건너뛴다 — Resolved일 때만 표시한다.
            outcome, item = extract_knowledge(
                case, mark_checked=(case.status == 'Resolved'))
        except Exception:
            logger.exception("case knowledge extraction failed (%s)", case.case_id)
            return Response({'error': self.ERROR_MESSAGES['failed']},
                            status=status.HTTP_502_BAD_GATEWAY)

        if outcome in self.ERROR_MESSAGES:
            return Response({'error': self.ERROR_MESSAGES[outcome], 'outcome': outcome},
                            status=status.HTTP_502_BAD_GATEWAY
                            if outcome == 'failed' else status.HTTP_400_BAD_REQUEST)

        log_event(request.user, 'knowledge_extract',
                  detail=f"{case.case_id} -> {item.knowledge_id} ({outcome})")
        return Response({'outcome': outcome,
                         'item': KnowledgeItemSerializer(item).data},
                        status=status.HTTP_201_CREATED if outcome == 'created'
                        else status.HTTP_200_OK)


class ChatKnowledgeExtractView(APIView):
    """POST /api/help-agent/sessions/<id>/knowledge/ — 대화에서 지식 추출.

    사용자가 대화가 유효한 결론에 도달했다고 판단했을 때 명시적으로 호출
    ("이 대화를 지식으로 저장" 버튼). AI가 시행착오를 걸러 문제-원인-해결
    또는 설정 절차/가이드를 정제해 KnowledgeItem(draft, 출처=chat_session)으로
    저장한다.
    본인 세션만 가능. 이미 추출된 세션이면 기존 항목을 돌려준다.
    """

    permission_classes = [IsEngineerOrAbove]

    ERROR_MESSAGES = {
        'no_knowledge': '이 대화에서는 재사용할 만한 지식(문제-해결 또는 설정 절차)을 '
                        '찾지 못했습니다. 해결책이나 구체적인 설정이 오간 대화에서 '
                        '다시 시도해주세요.',
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


class LabConfigView(APIView):
    """GET /api/labs/config/ — Lab Tests가 쓸 EVE-NG 서버가 설정돼 있는지.

    자격증명은 내려보내지 않는다(서버 주소만). 이 화면은 EVE-NG 없이도 열리므로,
    미설정일 때 빈 화면 대신 무엇을 해야 하는지 알려주기 위한 값이다.
    """

    permission_classes = [IsEngineerOrAbove]

    def get(self, request):
        configured = bool(settings.EVENG_URL and settings.EVENG_USER
                          and settings.EVENG_PASSWORD)
        return Response({
            'configured': configured,
            # 주소는 "어느 랩 서버를 보고 있나"를 확인하는 용도라 계정 없이 노출한다
            'server': settings.EVENG_URL,
        })


# ------------------------------------------------------------------ Lab Tests

def _lab_server():
    """.env가 가리키는 EVE-NG 서버 행을 얻는다(없으면 만든다).

    서버는 지금 하나뿐이지만 랩이 서버를 참조하게 해두면, 나중에 Pro 서버로
    옮길 때 전부 한 번에 넘기는 대신 랩 단위로 옮겨가며 검증할 수 있다.
    """
    from .models import LabServer
    if not eveng.is_configured():
        # 설정 없이 등록하면 base_url이 빈 서버 행이 생겨 나중에 랩이 붕 뜬다
        raise eveng.EvengNotConfigured(
            'EVE-NG 접속 정보가 없습니다. .env의 CASEFLOW_EVENG_URL / _USER / _PASSWORD를 확인하세요.')
    server, _ = LabServer.objects.get_or_create(base_url=settings.EVENG_URL)
    return server


def _eveng_error_response(exc):
    if isinstance(exc, eveng.EvengNotConfigured):
        return Response({'error': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    logger.warning('EVE-NG 요청 실패: %s', exc)
    return Response({'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)


class LabListView(generics.ListAPIView):
    """GET /api/labs/ — Case-Flow에 등록된 랩 목록 (엔지니어 이상)."""
    permission_classes = [IsEngineerOrAbove]
    serializer_class = LabSerializer

    def get_queryset(self):
        from .models import Lab
        return Lab.objects.select_related('server').prefetch_related('nodes')


class LabAvailableView(APIView):
    """GET /api/labs/available/ — EVE-NG에 있지만 아직 등록되지 않은 랩 (관리자).

    EVE-NG의 랩을 전부 노출하지 않는 이유: 다른 사람 작업용 랩이 섞여 있다.
    등록 화면에서 고르기 위한 후보 목록이다.
    """
    permission_classes = [IsAdminRole]

    def get(self, request):
        from .models import Lab
        try:
            server = _lab_server()
            client = eveng.EvengClient()
            labs = client.list_labs()
            version = client.server_version()
        except eveng.EvengError as e:
            return _eveng_error_response(e)

        if version and server.version != version:
            server.version = version
            server.checked_at = timezone.now()
            server.save(update_fields=['version', 'checked_at'])

        registered = set(Lab.objects.filter(server=server).values_list('path', flat=True))
        return Response({
            'server': server.base_url,
            'version': server.version,
            'labs': [
                {**lab, 'registered': lab['path'] in registered}
                for lab in sorted(labs, key=lambda x: x['path'])
            ],
        })


class LabRegisterView(APIView):
    """POST/DELETE /api/labs/register/ — 랩 등록·해제 (관리자).

    등록은 EVE-NG의 랩을 Case-Flow 메뉴에 올리는 것일 뿐, EVE-NG에는 아무
    영향을 주지 않는다. 해제도 마찬가지로 우리 쪽 기록만 지운다.
    """
    permission_classes = [IsAdminRole]

    def post(self, request):
        from .models import Lab
        path = (request.data.get('path') or '').strip()
        if not path:
            return Response({'error': '랩 경로가 필요합니다.'},
                            status=status.HTTP_400_BAD_REQUEST)
        name = (request.data.get('name') or '').strip()
        if not name:  # 파일명에서 확장자만 떼어 기본 이름으로
            name = path.rsplit('/', 1)[-1].removesuffix('.unl')

        try:
            server = _lab_server()
        except eveng.EvengError as e:
            return _eveng_error_response(e)

        lab, created = Lab.objects.get_or_create(
            server=server, path=path,
            defaults={
                'name': name[:200],
                'vendor': (request.data.get('vendor') or '').strip()[:50],
                'description': (request.data.get('description') or '').strip()[:300],
            },
        )
        if not created:
            return Response({'error': '이미 등록된 랩입니다.', 'id': lab.id},
                            status=status.HTTP_409_CONFLICT)
        return Response(LabSerializer(lab).data, status=status.HTTP_201_CREATED)

    def delete(self, request):
        from .models import Lab
        lab = Lab.objects.filter(id=request.data.get('id')).first()
        if lab is None:
            return Response({'error': '등록되지 않은 랩입니다.'},
                            status=status.HTTP_404_NOT_FOUND)
        name = lab.name
        lab.delete()  # 스냅샷은 CASCADE로 함께 삭제. EVE-NG는 건드리지 않는다.
        return Response({'message': f'{name} 등록을 해제했습니다.'})


class LabTopologyView(APIView):
    """GET /api/labs/<id>/ — 저장된 토폴로지 스냅샷 (엔지니어 이상)."""
    permission_classes = [IsEngineerOrAbove]

    def get(self, request, id):
        from .models import Lab
        lab = (Lab.objects.select_related('server')
               .prefetch_related('nodes', 'networks', 'links').filter(id=id).first())
        if lab is None:
            return Response({'error': '등록되지 않은 랩입니다.'},
                            status=status.HTTP_404_NOT_FOUND)
        return Response(LabDetailSerializer(lab).data)


class LabRefreshView(APIView):
    """POST /api/labs/<id>/refresh/ — EVE-NG에서 토폴로지를 다시 가져온다.

    EVE-NG 쪽은 읽기만 한다. 스냅샷은 통째로 갈아끼우되, 이름을 키로 삼아
    노드 행을 재사용한다 — eve_id·console 포트는 서버를 옮기면 재부여되므로
    갱신되는 값으로만 다룬다.
    """
    permission_classes = [IsEngineerOrAbove]

    def post(self, request, id):
        from .models import Lab, LabLink, LabNetwork, LabNode
        lab = Lab.objects.filter(id=id).first()
        if lab is None:
            return Response({'error': '등록되지 않은 랩입니다.'},
                            status=status.HTTP_404_NOT_FOUND)
        try:
            data = eveng.EvengClient().topology(lab.path)
        except eveng.EvengError as e:
            return _eveng_error_response(e)

        with transaction.atomic():
            seen = []
            for node in data['nodes']:
                LabNode.objects.update_or_create(
                    lab=lab, name=node['name'],
                    defaults={k: v for k, v in node.items() if k != 'name'})
                seen.append(node['name'])
            # EVE-NG에서 사라진 노드는 스냅샷에서도 지운다
            lab.nodes.exclude(name__in=seen).delete()

            # 네트워크·링크는 자체 식별자가 없어 통째로 다시 만든다
            lab.networks.all().delete()
            LabNetwork.objects.bulk_create(
                [LabNetwork(lab=lab, **net) for net in data['networks']])
            lab.links.all().delete()
            LabLink.objects.bulk_create(
                [LabLink(lab=lab, **link) for link in data['links']])

            lab.topology_synced_at = timezone.now()
            lab.save(update_fields=['topology_synced_at'])

        lab = (Lab.objects.select_related('server')
               .prefetch_related('nodes', 'networks', 'links').get(id=lab.id))
        return Response(LabDetailSerializer(lab).data)


class LabIconView(APIView):
    """GET /api/labs/icons/<filename> — EVE-NG 노드 아이콘 중계.

    브라우저가 EVE-NG에 직접 붙지 않게 한다(자격증명 비노출 + 사내망에서
    EVE-NG에 못 닿는 자리에서도 화면이 뜬다).
    """
    permission_classes = [IsEngineerOrAbove]

    def get(self, request, filename):
        try:
            content, content_type = eveng.EvengClient().icon(filename)
        except eveng.EvengError as e:
            return _eveng_error_response(e)
        response = HttpResponse(content, content_type=content_type)
        response['Cache-Control'] = 'private, max-age=86400'  # 아이콘은 잘 안 바뀐다
        return response
