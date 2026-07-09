import logging
from datetime import timedelta

from django.db.models import Count, Max, Q
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.views import APIView

from django.conf import settings

from .models import AppSetting, Case
from .serializers import CaseSerializer, CaseDetailSerializer
from .services.analyzer import (
    AVAILABLE_MODELS,
    TRANSLATION_MODEL_SETTING_KEY,
    detect_provider,
    get_translation_model,
    provider_api_key,
)
from .services.gmail_client import GmailAuthError
from .services.gmail_sync import sync_gmail

logger = logging.getLogger(__name__)


@api_view(['GET'])
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


class CaseDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = CASES_WITH_LAST_EMAIL
    serializer_class = CaseDetailSerializer
    lookup_field = 'id'


class TranslationModelView(APIView):
    """GET/PUT /api/settings/translation-model/ — AI 분석 모델 조회/변경.

    프론트에서 선택한 모델은 AppSetting(DB)에 저장되어 서버 재시작 후에도 유지되며,
    settings.py의 기본값보다 우선한다. {"model": "default"}를 보내면 기본값으로 복귀.
    """

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
