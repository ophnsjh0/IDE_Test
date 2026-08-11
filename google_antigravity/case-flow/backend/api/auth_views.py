"""세션 기반 로그인/로그아웃/현재 사용자 API.

계정 발급은 Django admin(/admin)에서 관리자가 수행한다.
로그인 성공 시 세션 쿠키(httpOnly)가 설정되고, 이후 쓰기 요청은
csrftoken 쿠키 값을 X-CSRFToken 헤더로 보내야 한다.
"""
import logging

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import SignupRequest, UserProfile
from .permissions import IsAdminRole, get_user_role, set_user_role
from .services import gmail_client
from .services.usage import log_event

logger = logging.getLogger(__name__)

VALID_ROLES = [choice[0] for choice in UserProfile.ROLE_CHOICES]
REQUESTABLE_ROLES = [choice[0] for choice in SignupRequest.REQUESTABLE_ROLE_CHOICES]
ROLE_LABELS_KO = {'viewer': '조회자', 'engineer': '엔지니어', 'admin': '관리자'}


def _user_payload(user):
    role = get_user_role(user)
    return {
        'authenticated': True,
        'username': user.username,
        'name': user.get_full_name() or user.username,
        'role': role,
        'is_admin': role == 'admin',
    }


@method_decorator(ensure_csrf_cookie, name='dispatch')
class LoginView(APIView):
    """POST /api/auth/login/ {username, password}"""
    permission_classes = [AllowAny]

    def post(self, request):
        username = (request.data.get('username') or '').strip()
        password = request.data.get('password') or ''
        user = authenticate(request, username=username, password=password)
        if user is None:
            return Response({'error': '아이디 또는 비밀번호가 올바르지 않습니다.'},
                            status=status.HTTP_401_UNAUTHORIZED)
        login(request, user)
        log_event(user, 'login')
        return Response(_user_payload(user))


class LogoutView(APIView):
    """POST /api/auth/logout/ — 서버 세션 폐기."""

    def post(self, request):
        logout(request)
        return Response({'authenticated': False})


@method_decorator(ensure_csrf_cookie, name='dispatch')
class MeView(APIView):
    """GET /api/auth/me/ — 로그인 상태 확인 + csrftoken 쿠키 발급.

    프론트가 앱 로드 시 호출한다. 미로그인도 200으로 응답해
    로그인 페이지 리다이렉트 판단은 프론트가 하도록 한다.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        if request.user.is_authenticated:
            return Response(_user_payload(request.user))
        return Response({'authenticated': False})


def _account_payload(user):
    return {
        'id': user.id,
        'username': user.username,
        'name': user.get_full_name(),
        'role': get_user_role(user),
        'is_active': user.is_active,
        'last_login': (timezone.localtime(user.last_login).strftime('%Y-%m-%d %H:%M')
                       if user.last_login else None),
        'date_joined': timezone.localtime(user.date_joined).strftime('%Y-%m-%d'),
    }


def _validate_email(email):
    """사내 연락처 검증. 형식만 확인하고 도메인은 막지 않는다 —
    어차피 소유 확인(인증 메일)을 하지 않아 도메인 제한은 실효가 없고,
    통제는 가입 알림을 받은 관리자의 사후 조치가 담당한다."""
    if not email:
        return '메일 주소를 입력하세요.'
    try:
        validate_email(email)
    except ValidationError:
        return '메일 주소 형식이 올바르지 않습니다.'
    return None


def _validate_new_password(password, user=None):
    """Django 비밀번호 정책 검증. 문제 없으면 None, 있으면 에러 메시지 반환."""
    try:
        validate_password(password, user=user)
        return None
    except ValidationError as e:
        return ' '.join(e.messages)


class UserListCreateView(APIView):
    """GET/POST /api/auth/users/ — 계정 목록 조회/발급 (관리자 전용)."""
    permission_classes = [IsAdminRole]

    def get(self, request):
        users = User.objects.order_by('-is_staff', 'username')
        return Response([_account_payload(u) for u in users])

    def post(self, request):
        username = (request.data.get('username') or '').strip()
        password = request.data.get('password') or ''
        name = (request.data.get('name') or '').strip()
        role = request.data.get('role') or 'viewer'

        if role not in VALID_ROLES:
            return Response({'error': f'유효하지 않은 역할입니다: {role}'},
                            status=status.HTTP_400_BAD_REQUEST)
        if not username:
            return Response({'error': '아이디를 입력하세요.'}, status=status.HTTP_400_BAD_REQUEST)
        if User.objects.filter(username__iexact=username).exists():
            return Response({'error': f'이미 존재하는 아이디입니다: {username}'},
                            status=status.HTTP_400_BAD_REQUEST)
        password_error = _validate_new_password(password)
        if password_error:
            return Response({'error': password_error}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.create_user(username=username, password=password,
                                        first_name=name)
        set_user_role(user, role)
        return Response(_account_payload(user), status=status.HTTP_201_CREATED)


class UserDetailView(APIView):
    """PATCH/DELETE /api/auth/users/<id>/ — 역할 변경, 활성/비활성 전환,
    비밀번호 재설정, 계정 삭제 (관리자 전용)."""
    permission_classes = [IsAdminRole]

    def delete(self, request, user_id):
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({'error': '존재하지 않는 계정입니다.'},
                            status=status.HTTP_404_NOT_FOUND)
        if user == request.user:
            return Response({'error': '자기 자신의 계정은 삭제할 수 없습니다.'},
                            status=status.HTTP_400_BAD_REQUEST)
        username = user.username
        user.delete()
        return Response({'deleted': username})

    def patch(self, request, user_id):
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({'error': '존재하지 않는 계정입니다.'},
                            status=status.HTTP_404_NOT_FOUND)

        if 'role' in request.data:
            role = request.data['role']
            if role not in VALID_ROLES:
                return Response({'error': f'유효하지 않은 역할입니다: {role}'},
                                status=status.HTTP_400_BAD_REQUEST)
            if user == request.user and role != 'admin':
                return Response({'error': '자기 자신의 관리자 권한은 해제할 수 없습니다.'},
                                status=status.HTTP_400_BAD_REQUEST)
            set_user_role(user, role)

        if 'is_active' in request.data:
            if user == request.user:
                return Response({'error': '자기 자신의 계정은 비활성화할 수 없습니다.'},
                                status=status.HTTP_400_BAD_REQUEST)
            user.is_active = bool(request.data['is_active'])

        if request.data.get('password'):
            password_error = _validate_new_password(request.data['password'], user=user)
            if password_error:
                return Response({'error': password_error}, status=status.HTTP_400_BAD_REQUEST)
            user.set_password(request.data['password'])

        user.save()
        return Response(_account_payload(user))


class SignupRequestView(APIView):
    """POST /api/auth/signup-requests/ — 로그인 화면의 계정 발급 (비로그인).

    2026-08-11부터 신청 즉시 계정을 만들고 바로 로그인할 수 있게 한다.
    승인 대기를 없앤 이유는 두 가지다. ① 승인자가 병목이 되어 파일럿 확산이
    느려졌고 ② 승인 링크가 GET이라 메일 보안 스캐너가 사람보다 먼저 눌러
    자동 승인되는 문제가 있었다(링크 자체를 없애 원인을 제거).

    통제는 사전 승인 대신 사후 조치로 옮겼다 — 관리자에게 가입 알림 메일이
    가고, 부적절한 가입은 계정 관리에서 역할 변경·비활성화로 처리한다.
    사내망에서만 접근 가능한 파일럿이라는 전제에서의 선택.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        username = (request.data.get('username') or '').strip()
        password = request.data.get('password') or ''
        name = (request.data.get('name') or '').strip()
        email = (request.data.get('email') or '').strip()
        reason = (request.data.get('reason') or '').strip()
        requested_role = request.data.get('requested_role') or 'viewer'

        if not username:
            return Response({'error': '아이디를 입력하세요.'}, status=status.HTTP_400_BAD_REQUEST)
        if requested_role not in REQUESTABLE_ROLES:
            return Response({'error': f'신청 가능한 역할이 아닙니다: {requested_role}'},
                            status=status.HTTP_400_BAD_REQUEST)
        if User.objects.filter(username__iexact=username).exists():
            return Response({'error': f'이미 존재하는 아이디입니다: {username}'},
                            status=status.HTTP_400_BAD_REQUEST)
        email_error = _validate_email(email)
        if email_error:
            return Response({'error': email_error}, status=status.HTTP_400_BAD_REQUEST)
        password_error = _validate_new_password(password)
        if password_error:
            return Response({'error': password_error}, status=status.HTTP_400_BAD_REQUEST)

        user = User(username=username, first_name=name, email=email)
        user.set_password(password)
        user.save()
        set_user_role(user, requested_role)
        signup = SignupRequest.objects.create(
            username=username, name=name, email=email, reason=reason[:300],
            requested_role=requested_role, status='approved',
            approved_at=timezone.now(),
        )
        logger.info("Signup: %s (%s) created", username, requested_role)

        # 알림 실패가 가입을 막지 않는다 — 계정은 이미 정상 생성됐다.
        self._notify_admin(signup)
        return Response({'message': '계정이 생성되었습니다. 바로 로그인하실 수 있습니다.'},
                        status=status.HTTP_201_CREATED)

    @staticmethod
    def _notify_admin(signup):
        joined_at = timezone.localtime(signup.created_at).strftime('%Y-%m-%d %H:%M')
        html = f"""
        <div style="font-family:sans-serif;max-width:520px">
          <h2>Case-Flow 새 사용자 가입</h2>
          <table cellpadding="6" style="border-collapse:collapse">
            <tr><td><b>아이디</b></td><td>{signup.username}</td></tr>
            <tr><td><b>이름</b></td><td>{signup.name or '-'}</td></tr>
            <tr><td><b>메일</b></td><td>{signup.email or '-'}</td></tr>
            <tr><td><b>신청 사유</b></td><td>{signup.reason or '-'}</td></tr>
            <tr><td><b>부여된 역할</b></td><td>{ROLE_LABELS_KO[signup.requested_role]}</td></tr>
            <tr><td><b>가입 시각</b></td><td>{joined_at}</td></tr>
          </table>
          <p style="margin-top:16px">이미 로그인 가능한 상태입니다. 모르는 사용자라면
             계정 관리에서 역할을 낮추거나 비활성화하세요.</p>
          <p><a href="{settings.APP_BASE_URL}/users"
                style="background:#228be6;color:#fff;padding:10px 20px;border-radius:6px;
                       text-decoration:none;font-weight:bold">계정 관리 열기</a></p>
        </div>
        """
        try:
            gmail_client.send_email(
                settings.SIGNUP_APPROVER_EMAIL,
                f'[Case-Flow] 새 사용자 가입: {signup.username}',
                html,
            )
        except Exception:
            logger.exception("Signup notification mail failed for %s", signup.username)


