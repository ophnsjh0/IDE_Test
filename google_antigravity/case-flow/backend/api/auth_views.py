"""세션 기반 로그인/로그아웃/현재 사용자 API.

계정 발급은 Django admin(/admin)에서 관리자가 수행한다.
로그인 성공 시 세션 쿠키(httpOnly)가 설정되고, 이후 쓰기 요청은
csrftoken 쿠키 값을 X-CSRFToken 헤더로 보내야 한다.
"""
import logging

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.exceptions import ValidationError
from django.http import HttpResponse
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

logger = logging.getLogger(__name__)

SIGNUP_TOKEN_SALT = 'caseflow-signup-approval'

VALID_ROLES = [choice[0] for choice in UserProfile.ROLE_CHOICES]


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
    """PATCH /api/auth/users/<id>/ — 역할 변경, 활성/비활성 전환, 비밀번호 재설정 (관리자 전용)."""
    permission_classes = [IsAdminRole]

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
    """POST /api/auth/signup-requests/ — 로그인 화면의 계정 발급 요청 (비로그인).

    요청 정보를 저장하고 승인자에게 서명된 승인 링크가 담긴 메일을 보낸다.
    비밀번호는 해시로만 저장하며 메일에는 포함하지 않는다.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        username = (request.data.get('username') or '').strip()
        password = request.data.get('password') or ''
        name = (request.data.get('name') or '').strip()
        reason = (request.data.get('reason') or '').strip()

        if not username:
            return Response({'error': '아이디를 입력하세요.'}, status=status.HTTP_400_BAD_REQUEST)
        if User.objects.filter(username__iexact=username).exists():
            return Response({'error': f'이미 존재하는 아이디입니다: {username}'},
                            status=status.HTTP_400_BAD_REQUEST)
        if SignupRequest.objects.filter(username__iexact=username, status='pending').exists():
            return Response({'error': '같은 아이디로 승인 대기 중인 요청이 있습니다.'},
                            status=status.HTTP_400_BAD_REQUEST)
        password_error = _validate_new_password(password)
        if password_error:
            return Response({'error': password_error}, status=status.HTTP_400_BAD_REQUEST)

        signup = SignupRequest.objects.create(
            username=username, name=name, reason=reason[:300],
            password_hash=make_password(password),
        )

        token = signing.dumps({'id': signup.id}, salt=SIGNUP_TOKEN_SALT)
        approve_url = request.build_absolute_uri(f'/api/auth/signup-approve/?token={token}')
        requested_at = timezone.localtime(signup.created_at).strftime('%Y-%m-%d %H:%M')
        html = f"""
        <div style="font-family:sans-serif;max-width:520px">
          <h2>Case-Flow 계정 발급 요청</h2>
          <table cellpadding="6" style="border-collapse:collapse">
            <tr><td><b>아이디</b></td><td>{signup.username}</td></tr>
            <tr><td><b>이름</b></td><td>{signup.name or '-'}</td></tr>
            <tr><td><b>요청 사유</b></td><td>{signup.reason or '-'}</td></tr>
            <tr><td><b>요청 시각</b></td><td>{requested_at}</td></tr>
            <tr><td><b>생성될 역할</b></td><td>조회자 (승인 후 계정 관리에서 변경 가능)</td></tr>
          </table>
          <p style="margin-top:20px">
            <a href="{approve_url}"
               style="background:#228be6;color:#fff;padding:12px 24px;border-radius:6px;
                      text-decoration:none;font-weight:bold">계정 생성 승인</a>
          </p>
          <p style="color:#868e96;font-size:13px">
            링크는 7일간 유효합니다. 승인하지 않으려면 이 메일을 무시하세요.
          </p>
        </div>
        """
        try:
            gmail_client.send_email(
                settings.SIGNUP_APPROVER_EMAIL,
                f'[Case-Flow] 계정 발급 요청: {signup.username}',
                html,
            )
        except Exception:
            logger.exception("Signup approval mail failed for %s", signup.username)
            signup.delete()  # 재요청 가능하도록 대기 상태를 남기지 않음
            return Response(
                {'error': '승인 메일 발송에 실패했습니다. 관리자에게 직접 문의하세요.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response({'message': '요청이 접수되었습니다. 관리자 승인 후 로그인할 수 있습니다.'},
                        status=status.HTTP_201_CREATED)


class SignupApproveView(APIView):
    """GET /api/auth/signup-approve/?token=... — 승인 메일의 버튼 링크.

    메일 클라이언트 브라우저에서 로그인 없이 열리므로 서명 토큰으로 검증하고,
    사람이 읽을 HTML 페이지로 응답한다.
    """
    permission_classes = [AllowAny]

    @staticmethod
    def _page(title, body, ok=True):
        color = '#2f9e44' if ok else '#e03131'
        return HttpResponse(f"""
        <html><head><meta charset="utf-8"><title>Case-Flow</title></head>
        <body style="font-family:sans-serif;display:flex;justify-content:center;padding-top:80px">
          <div style="max-width:480px;text-align:center">
            <h2 style="color:{color}">{title}</h2>
            <p style="color:#495057">{body}</p>
          </div>
        </body></html>
        """)

    def get(self, request):
        token = request.query_params.get('token', '')
        try:
            payload = signing.loads(token, salt=SIGNUP_TOKEN_SALT,
                                    max_age=settings.SIGNUP_APPROVAL_MAX_AGE)
        except signing.SignatureExpired:
            return self._page('링크가 만료되었습니다', '승인 링크는 7일간 유효합니다. 요청자에게 재요청을 안내하세요.', ok=False)
        except signing.BadSignature:
            return self._page('유효하지 않은 링크입니다', '링크가 손상되었거나 위조되었습니다.', ok=False)

        signup = SignupRequest.objects.filter(id=payload.get('id')).first()
        if signup is None:
            return self._page('요청을 찾을 수 없습니다', '이미 삭제된 요청입니다.', ok=False)
        if signup.status == 'approved':
            return self._page('이미 처리된 요청입니다', f'{signup.username} 계정은 이미 생성되어 있습니다.')
        if User.objects.filter(username__iexact=signup.username).exists():
            return self._page('생성 불가', f'{signup.username} 아이디가 이미 사용 중입니다.', ok=False)

        user = User(username=signup.username, first_name=signup.name,
                    password=signup.password_hash)
        user.save()
        set_user_role(user, 'viewer')
        signup.status = 'approved'
        signup.approved_at = timezone.now()
        signup.save()
        logger.info("Signup approved: %s", signup.username)

        return self._page('계정이 생성되었습니다',
                          f'<b>{signup.username}</b> 계정이 조회자 역할로 생성되었습니다. '
                          '요청자에게 로그인 가능함을 알려주시고, 필요하면 계정 관리에서 역할을 조정하세요.')
