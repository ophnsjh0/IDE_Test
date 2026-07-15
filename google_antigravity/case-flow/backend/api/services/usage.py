"""사용 로그 기록 — 파일럿 기간 도입 확대 판단 지표 수집.

기록은 부가 기능이므로 어떤 예외도 호출자에게 전파하지 않는다.
"""
import logging

from ..models import UsageEvent

logger = logging.getLogger(__name__)

DETAIL_MAX = 300


def log_event(user, event, detail=''):
    """이벤트 1건 기록. 미인증 사용자는 user=None으로 남긴다."""
    try:
        UsageEvent.objects.create(
            user=user if getattr(user, 'is_authenticated', False) else None,
            event=event,
            detail=(detail or '')[:DETAIL_MAX],
        )
    except Exception:
        logger.warning("usage log failed (%s)", event, exc_info=True)
