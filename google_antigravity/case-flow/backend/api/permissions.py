"""역할 기반 권한. 실제 보안 경계는 여기(서버)이고, 프론트 버튼 숨김은 UX일 뿐이다.

역할 등급: viewer(0) < engineer(1) < admin(2)
- viewer: 케이스/대시보드 조회만
- engineer: + 케이스 생성/수정, Gmail 동기화
- admin: + 케이스 삭제, AI 모델 변경, 계정 관리
"""
from rest_framework.permissions import BasePermission

from .models import UserProfile

ROLE_LEVELS = {'viewer': 0, 'engineer': 1, 'admin': 2}


def get_user_role(user):
    """프로필의 role을 반환. 프로필이 없는 계정(장고 admin에서 직접 생성 등)은
    is_staff/superuser면 admin, 아니면 engineer로 간주 (도입 전 계정과 동일한 정책)."""
    if not user.is_authenticated:
        return None
    try:
        return user.profile.role
    except UserProfile.DoesNotExist:
        return 'admin' if (user.is_staff or user.is_superuser) else 'engineer'


def set_user_role(user, role):
    """역할 변경 + Django admin 접근용 is_staff 동기화."""
    UserProfile.objects.update_or_create(user=user, defaults={'role': role})
    user.is_staff = (role == 'admin') or user.is_superuser
    user.save(update_fields=['is_staff'])


def _has_role_level(user, minimum):
    role = get_user_role(user)
    return role is not None and ROLE_LEVELS[role] >= ROLE_LEVELS[minimum]


class IsEngineerOrAbove(BasePermission):
    message = '엔지니어 이상의 권한이 필요합니다.'

    def has_permission(self, request, view):
        return _has_role_level(request.user, 'engineer')


class IsAdminRole(BasePermission):
    message = '관리자 권한이 필요합니다.'

    def has_permission(self, request, view):
        return _has_role_level(request.user, 'admin')
