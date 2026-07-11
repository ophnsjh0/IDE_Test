import logging
from datetime import timedelta

import anthropic
from django.db.models import Count, Max, Q
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from django.conf import settings

from .models import AppSetting, Case
from .permissions import IsAdminRole, IsEngineerOrAbove
from .serializers import CaseSerializer, CaseDetailSerializer
from .services.analyzer import (
    AVAILABLE_MODELS,
    TRANSLATION_MODEL_SETTING_KEY,
    detect_provider,
    get_translation_model,
    provider_api_key,
)
from .services import help_agent
from .services.gmail_client import GmailAuthError
from .services.gmail_sync import sync_gmail

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
        except GmailAuthError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception("Gmail sync failed")
            return Response(
                {'error': 'Gmail 동기화 중 오류가 발생했습니다. 서버 로그를 확인하세요.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(summary)


class HelpAgentChatView(APIView):
    """POST /api/help-agent/chat/ — 케이스 DB 검색 헬프 에이전트와 대화.

    읽기 전용 도구만 쓰므로 viewer 이상 누구나 사용 가능 (기본 인증만 요구).
    본문: {"messages": [{"role": "user"|"assistant", "content": "..."}]}
    """

    MAX_CONTENT_LENGTH = 4000

    def post(self, request):
        messages = request.data.get('messages')
        error = self._validate(messages)
        if error:
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

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
        return Response(result)

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
