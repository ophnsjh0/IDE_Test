from django.test import TestCase
from django.utils import timezone

from .models import Case, CaseEmail
from .services.email_parser import extract_device_info, normalize_body
from .services.gmail_sync import _find_case, apply_device_info


def make_case(**kwargs):
    defaults = dict(vendor='Arista', status='Open', summary='요약', source='email')
    defaults.update(kwargs)
    return Case.objects.create(**defaults)


def make_email(case, subject, thread_id='thread-x', message_id=None):
    return CaseEmail.objects.create(
        case=case,
        gmail_message_id=message_id or f'msg-{CaseEmail.objects.count() + 1}',
        gmail_thread_id=thread_id,
        direction='outbound',
        sender='eng@ubersys.co.kr',
        recipient='support@arista.com',
        subject=subject,
        subject_ko='',
        body_original='본문',
        body_ko='',
        received_at=timezone.now(),
    )


class FindCaseTests(TestCase):
    """Arista처럼 오픈 메일과 SR 확인 메일이 다른 스레드로 갈릴 때의 매칭."""

    def test_confirmation_mail_matches_original_case_by_embedded_subject(self):
        # 엔지니어 오픈 메일로 생성된 케이스 (SR 번호 없음)
        case = make_case(gmail_thread_id='thread-original')
        make_email(case, '40G Interface Link FLAP', thread_id='thread-original')

        # 벤더 확인 메일: 새 스레드 + SR 번호 + 원본 제목 포함
        found = _find_case(
            '834065', 'thread-sr', 'Arista',
            'New UBER Systems Co. Ltd Case: SR 834065 40G Interface Link FLAP [ ref:!00DA0.!500Kh0 ]',
        )
        self.assertEqual(found, case)

    def test_original_mail_matches_confirmation_case_in_reverse_order(self):
        # SR 확인 메일이 먼저 동기화되어 케이스가 이미 있는 경우
        case = make_case(vendor_case_number='825808', gmail_thread_id='thread-sr')
        make_email(
            case,
            'New UBER Systems Co. Ltd Case: SR 825808 [samsung securities] '
            'Continuous PhyEthtool Logs After EOS Upgrade',
            thread_id='thread-sr',
        )

        found = _find_case(
            None, 'thread-original', 'Arista',
            '[samsung securities] Continuous PhyEthtool Logs After EOS Upgrade',
        )
        self.assertEqual(found, case)

    def test_repeated_notification_subjects_do_not_merge(self):
        # 번호 없는 공지 메일끼리는 제목이 같아도 병합 대상이 아님
        case = make_case(gmail_thread_id='thread-notice-1')
        make_email(case, 'New Field notice email notification', thread_id='thread-notice-1')

        found = _find_case(None, 'thread-notice-2', 'Arista',
                           'New Field notice email notification')
        self.assertIsNone(found)

    def test_short_subject_does_not_match(self):
        case = make_case(gmail_thread_id='thread-short')
        make_email(case, 'Link FLAP', thread_id='thread-short')

        found = _find_case('834065', 'thread-new', 'Arista',
                           'New Case: SR 834065 Link FLAP')
        self.assertIsNone(found)

    def test_other_vendor_is_not_matched(self):
        case = make_case(gmail_thread_id='thread-a10')
        make_email(case, '40G Interface Link FLAP', thread_id='thread-a10')

        found = _find_case('834065', 'thread-new', 'A10',
                           'New UBER Systems Co. Ltd Case: SR 834065 40G Interface Link FLAP')
        self.assertIsNone(found)

    def test_thread_of_merged_case_is_traced_via_email(self):
        # 병합으로 케이스 대표 스레드가 아니게 된 스레드도 이메일로 역추적
        case = make_case(vendor_case_number='834065', gmail_thread_id='thread-sr')
        make_email(case, '40G Interface Link FLAP', thread_id='thread-original')

        found = _find_case(None, 'thread-original', 'Arista', 'Re: 40G Interface Link FLAP')
        self.assertEqual(found, case)

    def test_vendor_case_number_still_matches_first(self):
        case = make_case(vendor_case_number='834065', gmail_thread_id='thread-sr')
        found = _find_case('834065', 'unrelated-thread', 'Arista', '아무 제목')
        self.assertEqual(found, case)


CASE_OPEN_BODY = (
    '1. End customer name: NHN\n'
    '2. Partner/Reseller name: ubersystems\n'
    '3. Hardware Platform: TH1040-F\n'
    '4. Software Version: 6.0.8\n'
    '5. Priority : P2\n'
    '6. Serial Number : TH10154022070160\n'
    '7. Description :\n\n'
    'Hi Team\n'
    'After the following logs occurred, the device failed over.\n'
    'Jul 10 2026 04:00:35 Info [HA]:VRRP-A parid 0 vrid 1 state switch '
    'from 1 to 0 (Standby)\n'
    'Jul 10 2026 04:00:36 Info [HA]:VRRP-A parid 0 vrid 1 received higher '
    'priority advertisement from peer, transitioning to backup state now.\n'
    'Please check the attached show techsupport output and let us know the '
    'root cause of this failover event as soon as possible. Thanks.\n'
)


class FindCaseByBodyTests(TestCase):
    """제목을 바꿔 재발송해 스레드가 갈린 동일 접수 메일의 본문 유사도 매칭."""

    def test_resent_mail_with_new_subject_matches_by_body(self):
        case = make_case(vendor='A10', gmail_thread_id='thread-1')
        make_email(case, '[NHN-6.0.8]Device Failover Occurrence',
                   thread_id='thread-1')
        email = CaseEmail.objects.get(case=case)
        email.body_original = CASE_OPEN_BODY
        email.save()

        found = _find_case(
            None, 'thread-2', 'A10',
            '[NHN-6.0.8][AXMON]:Detected problem in Health Monitor',
            CASE_OPEN_BODY + '\nBest regards',
        )
        self.assertEqual(found, case)
        # 병합 표시가 타임라인에 남는다
        self.assertIn('중복 접수 메일', found.action_steps or '')

    def test_different_issue_creates_new_case(self):
        case = make_case(vendor='A10', gmail_thread_id='thread-1')
        make_email(case, '[NHN-6.0.8]Device Failover Occurrence',
                   thread_id='thread-1')
        email = CaseEmail.objects.get(case=case)
        email.body_original = CASE_OPEN_BODY
        email.save()

        other_body = (
            '1. End customer name: Kakao\n'
            '2. Hardware Platform: TH3350\n'
            '3. Serial Number : TH33500000000001\n'
            'Hello, we observed SNMP polling failures on this device after '
            'enabling the new monitoring profile. The walk stops responding '
            'after roughly ten minutes and only recovers when the agent is '
            'restarted manually. Please advise which debug output you need.\n'
        )
        found = _find_case(None, 'thread-2', 'A10', 'SNMP polling issue', other_body)
        self.assertIsNone(found)

    def test_short_body_is_skipped(self):
        case = make_case(vendor='A10', gmail_thread_id='thread-1')
        make_email(case, '[NHN-6.0.8]Device Failover Occurrence',
                   thread_id='thread-1')
        found = _find_case(None, 'thread-2', 'A10', '제목', '감사합니다.')
        self.assertIsNone(found)

    def test_matching_serial_number_relaxes_threshold(self):
        # 공통부(시리얼 포함) + 서로 다른 꼬리말로 유사도를 약 0.92로 구성
        # (ratio = 공통길이/(공통길이+꼬리길이) 이므로 꼬리를 공통부의 8.7%로)
        common = CASE_OPEN_BODY + 'filler word ' * 30
        tail = int(len(normalize_body(common)) * 0.087)
        body_a = common + 'x' * tail
        body_b = common + 'y' * tail

        case = make_case(vendor='A10', gmail_thread_id='thread-1')
        make_email(case, '[NHN-6.0.8]Device Failover Occurrence',
                   thread_id='thread-1')
        email = CaseEmail.objects.get(case=case)
        email.body_original = body_a
        email.save()

        found = _find_case(None, 'thread-2', 'A10', '재발송 제목', body_b)
        self.assertEqual(found, case)

        # 시리얼이 다르면 완화 없이 0.95가 적용되어 매칭되지 않는다
        email.body_original = body_a.replace('TH10154022070160', 'TH99999999999999')
        email.save()
        case.refresh_from_db()
        found = _find_case(None, 'thread-3', 'A10', '재발송 제목', body_b)
        self.assertIsNone(found)

    def test_other_vendor_body_is_not_compared(self):
        case = make_case(vendor='A10', gmail_thread_id='thread-1')
        make_email(case, '[NHN-6.0.8]Device Failover Occurrence',
                   thread_id='thread-1')
        email = CaseEmail.objects.get(case=case)
        email.body_original = CASE_OPEN_BODY
        email.save()

        found = _find_case(None, 'thread-2', 'Arista', '다른 벤더 재발송', CASE_OPEN_BODY)
        self.assertIsNone(found)


class ExtractDeviceInfoTests(TestCase):
    """메일 본문/제목에서 장비 모델·시리얼·버전 추출."""

    def test_a10_open_template(self):
        info = extract_device_info('[NHN-6.0.8]Device Failover Occurrence', CASE_OPEN_BODY)
        self.assertEqual(info['device_model'], 'TH1040-F')
        self.assertEqual(info['device_serial'], 'TH10154022070160')
        self.assertEqual(info['software_version'], '6.0.8')

    def test_hpe_sn_line_items_joined(self):
        body = (
            'RMA parts list:\n'
            'EC-ADV-AAS-UL, S/N 001BBC04E53A\n'
            'EC-BOOST-AAS-10G, S/N 001BBC04E53B\n'
            'EC-DTD-AAS, S/N 001BBC04E53C\n'
        )
        info = extract_device_info('RMA request', body)
        self.assertEqual(info['device_serial'],
                         '001BBC04E53A, 001BBC04E53B, 001BBC04E53C')

    def test_arista_model_token_and_subject_version(self):
        body = 'We upgraded our DCS-7050SX3-48YC12 switch and see PhyEthtool errors.'
        info = extract_device_info('[samsung-4.32.4M] PhyEthtool errors', body)
        self.assertEqual(info['device_model'], 'DCS-7050SX3-48YC12')
        self.assertEqual(info['software_version'], '4.32.4M')

    def test_no_device_info_returns_empty(self):
        info = extract_device_info('New End of Sale email notification',
                                   'The following products reach end of sale next quarter.')
        self.assertEqual(info, {'device_model': '', 'device_serial': '',
                                'software_version': ''})


class ApplyDeviceInfoTests(TestCase):
    """추출값 반영: 정규식 1차 -> AI 2차, 빈 필드만 채움."""

    def test_regex_first_then_ai_fills_missing(self):
        case = make_case(vendor='A10')
        analysis = {'device_model': 'TH9999', 'device_serial': 'AI-SERIAL',
                    'software_version': '9.9.9'}
        # 본문 정규식에서 모델/시리얼/버전을 모두 찾으므로 AI값은 무시된다
        apply_device_info(case, '[NHN-6.0.8] subject', CASE_OPEN_BODY, analysis)
        self.assertEqual(case.device_model, 'TH1040-F')
        self.assertEqual(case.device_serial, 'TH10154022070160')
        self.assertEqual(case.software_version, '6.0.8')

    def test_ai_value_used_when_regex_misses(self):
        case = make_case(vendor='HPE Aruba')
        analysis = {'device_model': 'Aruba 7205', 'device_serial': '',
                    'software_version': '8.10.0.9'}
        apply_device_info(case, 'Gateway issue', '컨트롤러에서 Role 소실 이슈가 발생했습니다.', analysis)
        self.assertEqual(case.device_model, 'Aruba 7205')
        self.assertEqual(case.software_version, '8.10.0.9')
        self.assertEqual(case.device_serial, '')

    def test_existing_values_are_not_overwritten(self):
        case = make_case(vendor='A10', device_model='TH1040-F')
        apply_device_info(case, 'subject', 'Model: TH3350', None)
        self.assertEqual(case.device_model, 'TH1040-F')


class AuthTests(TestCase):
    """세션 인증: 로그인 없이는 API 접근 불가, 로그인/로그아웃 플로우."""

    def setUp(self):
        from django.contrib.auth.models import User
        User.objects.create_user('eng1', password='test-pass-123!')

    def test_api_requires_login(self):
        response = self.client.get('/api/cases/')
        self.assertIn(response.status_code, (401, 403))

    def test_health_check_is_open(self):
        response = self.client.get('/api/health/')
        self.assertEqual(response.status_code, 200)

    def test_login_grants_access_and_me_reports_user(self):
        response = self.client.post('/api/auth/login/',
                                    {'username': 'eng1', 'password': 'test-pass-123!'},
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['username'], 'eng1')
        # csrftoken 쿠키가 함께 발급된다
        self.assertIn('csrftoken', response.cookies)

        self.assertEqual(self.client.get('/api/cases/').status_code, 200)
        me = self.client.get('/api/auth/me/').json()
        self.assertTrue(me['authenticated'])

    def test_wrong_password_rejected(self):
        response = self.client.post('/api/auth/login/',
                                    {'username': 'eng1', 'password': 'wrong'},
                                    content_type='application/json')
        self.assertEqual(response.status_code, 401)

    def test_me_reports_anonymous_without_session(self):
        me = self.client.get('/api/auth/me/')
        self.assertEqual(me.status_code, 200)
        self.assertFalse(me.json()['authenticated'])

    def test_logout_revokes_session(self):
        self.client.post('/api/auth/login/',
                         {'username': 'eng1', 'password': 'test-pass-123!'},
                         content_type='application/json')
        self.assertEqual(self.client.post('/api/auth/logout/').status_code, 200)
        self.assertIn(self.client.get('/api/cases/').status_code, (401, 403))


class UserManagementTests(TestCase):
    """관리자 전용 계정 발급/관리 API."""

    def setUp(self):
        from django.contrib.auth.models import User
        User.objects.create_user('staff1', password='admin-pass-123!', is_staff=True)
        User.objects.create_user('normal1', password='normal-pass-123!')

    def login(self, username, password):
        return self.client.post('/api/auth/login/',
                                {'username': username, 'password': password},
                                content_type='application/json')

    def test_normal_user_cannot_access(self):
        self.login('normal1', 'normal-pass-123!')
        self.assertEqual(self.client.get('/api/auth/users/').status_code, 403)
        self.assertEqual(self.client.post('/api/auth/users/', {},
                                          content_type='application/json').status_code, 403)

    def test_admin_creates_account(self):
        self.login('staff1', 'admin-pass-123!')
        response = self.client.post(
            '/api/auth/users/',
            {'username': 'eng2', 'password': 'good-pass-77!', 'name': '김엔지니어'},
            content_type='application/json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['username'], 'eng2')
        self.assertEqual(response.json()['role'], 'viewer')
        # 발급된 계정으로 로그인 가능
        self.client.post('/api/auth/logout/')
        self.assertEqual(self.login('eng2', 'good-pass-77!').status_code, 200)

    def test_duplicate_username_rejected(self):
        self.login('staff1', 'admin-pass-123!')
        response = self.client.post('/api/auth/users/',
                                    {'username': 'Normal1', 'password': 'good-pass-77!'},
                                    content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_weak_password_rejected(self):
        self.login('staff1', 'admin-pass-123!')
        response = self.client.post('/api/auth/users/',
                                    {'username': 'eng3', 'password': '1234'},
                                    content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_deactivate_blocks_login_and_self_deactivation_denied(self):
        from django.contrib.auth.models import User
        self.login('staff1', 'admin-pass-123!')
        normal = User.objects.get(username='normal1')
        staff = User.objects.get(username='staff1')

        # 자기 자신 비활성화는 거부
        response = self.client.patch(f'/api/auth/users/{staff.id}/',
                                     {'is_active': False}, content_type='application/json')
        self.assertEqual(response.status_code, 400)

        # 다른 계정 비활성화 -> 로그인 차단
        response = self.client.patch(f'/api/auth/users/{normal.id}/',
                                     {'is_active': False}, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.client.post('/api/auth/logout/')
        self.assertEqual(self.login('normal1', 'normal-pass-123!').status_code, 401)

    def test_password_reset(self):
        from django.contrib.auth.models import User
        self.login('staff1', 'admin-pass-123!')
        normal = User.objects.get(username='normal1')
        response = self.client.patch(f'/api/auth/users/{normal.id}/',
                                     {'password': 'new-pass-88!'},
                                     content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.client.post('/api/auth/logout/')
        self.assertEqual(self.login('normal1', 'new-pass-88!').status_code, 200)


class RolePermissionTests(TestCase):
    """역할(viewer/engineer/admin)별 API 권한 경계."""

    def setUp(self):
        from django.contrib.auth.models import User
        from .permissions import set_user_role
        for username, role in (('v1', 'viewer'), ('e1', 'engineer'), ('a1', 'admin')):
            user = User.objects.create_user(username, password='role-pass-123!')
            set_user_role(user, role)
        self.case = make_case(vendor='A10', summary='권한 테스트용 케이스')

    def login(self, username):
        self.client.post('/api/auth/login/',
                         {'username': username, 'password': 'role-pass-123!'},
                         content_type='application/json')

    def test_viewer_can_read_but_not_write(self):
        self.login('v1')
        self.assertEqual(self.client.get('/api/cases/').status_code, 200)
        self.assertEqual(self.client.get(f'/api/cases/{self.case.id}/').status_code, 200)
        self.assertEqual(self.client.get('/api/dashboard/stats/').status_code, 200)

        create = self.client.post('/api/cases/', {'vendor': 'A10', 'summary': '뷰어 생성 시도'},
                                  content_type='application/json')
        self.assertEqual(create.status_code, 403)
        patch = self.client.patch(f'/api/cases/{self.case.id}/', {'status': 'Resolved'},
                                  content_type='application/json')
        self.assertEqual(patch.status_code, 403)
        sync = self.client.post('/api/gmail/sync/')
        self.assertEqual(sync.status_code, 403)

    def test_engineer_can_write_but_not_delete_or_configure(self):
        self.login('e1')
        patch = self.client.patch(f'/api/cases/{self.case.id}/', {'status': 'Resolved'},
                                  content_type='application/json')
        self.assertEqual(patch.status_code, 200)

        self.assertEqual(self.client.delete(f'/api/cases/{self.case.id}/').status_code, 403)
        model_put = self.client.put('/api/settings/translation-model/', {'model': 'default'},
                                    content_type='application/json')
        self.assertEqual(model_put.status_code, 403)
        self.assertEqual(self.client.get('/api/auth/users/').status_code, 403)

    def test_admin_can_delete_case(self):
        self.login('a1')
        self.assertEqual(self.client.delete(f'/api/cases/{self.case.id}/').status_code, 204)

    def test_admin_changes_role_but_cannot_demote_self(self):
        from django.contrib.auth.models import User
        self.login('a1')
        e1 = User.objects.get(username='e1')
        response = self.client.patch(f'/api/auth/users/{e1.id}/', {'role': 'viewer'},
                                     content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['role'], 'viewer')

        a1 = User.objects.get(username='a1')
        response = self.client.patch(f'/api/auth/users/{a1.id}/', {'role': 'engineer'},
                                     content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_me_includes_role(self):
        self.login('e1')
        me = self.client.get('/api/auth/me/').json()
        self.assertEqual(me['role'], 'engineer')
        self.assertFalse(me['is_admin'])


class SignupRequestTests(TestCase):
    """계정 발급 요청 -> 승인 메일 -> 링크 클릭으로 계정 생성."""

    def request_signup(self, **overrides):
        from unittest.mock import patch
        data = {'username': 'newbie', 'password': 'newbie-pass-77!',
                'name': '신입', 'reason': '케이스 조회 필요'}
        data.update(overrides)
        with patch('api.auth_views.gmail_client.send_email') as mock_send:
            response = self.client.post('/api/auth/signup-requests/', data,
                                        content_type='application/json')
        return response, mock_send

    def extract_approve_url(self, mock_send):
        import re
        html = mock_send.call_args[0][2]
        match = re.search(r'href="([^"]+)"', html)
        return match.group(1)

    def test_request_sends_approval_mail_without_password(self):
        response, mock_send = self.request_signup()
        self.assertEqual(response.status_code, 201)
        mock_send.assert_called_once()
        to, subject, html = mock_send.call_args[0]
        self.assertEqual(to, 'jhshin@ubersys.co.kr')
        self.assertIn('newbie', html)
        self.assertNotIn('newbie-pass-77!', html)  # 비밀번호는 메일에 없음

    def test_approve_link_creates_account_with_requested_password(self):
        from django.contrib.auth.models import User
        from api.permissions import get_user_role
        _, mock_send = self.request_signup()
        url = self.extract_approve_url(mock_send)

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('계정이 생성되었습니다', response.content.decode())

        user = User.objects.get(username='newbie')
        self.assertEqual(get_user_role(user), 'viewer')
        # 요청 시 입력한 비밀번호로 로그인 가능
        login = self.client.post('/api/auth/login/',
                                 {'username': 'newbie', 'password': 'newbie-pass-77!'},
                                 content_type='application/json')
        self.assertEqual(login.status_code, 200)

    def test_approve_link_is_idempotent(self):
        from django.contrib.auth.models import User
        _, mock_send = self.request_signup()
        url = self.extract_approve_url(mock_send)
        self.client.get(url)
        response = self.client.get(url)  # 두 번째 클릭
        self.assertIn('이미 처리된', response.content.decode())
        self.assertEqual(User.objects.filter(username='newbie').count(), 1)

    def test_tampered_token_rejected(self):
        from django.contrib.auth.models import User
        _, mock_send = self.request_signup()
        url = self.extract_approve_url(mock_send)
        response = self.client.get(url[:-4] + 'xxxx')
        self.assertIn('유효하지 않은', response.content.decode())
        self.assertFalse(User.objects.filter(username='newbie').exists())

    def test_duplicate_username_or_pending_rejected(self):
        from django.contrib.auth.models import User
        User.objects.create_user('taken', password='x-pass-123!')
        response, _ = self.request_signup(username='taken')
        self.assertEqual(response.status_code, 400)

        self.request_signup()  # pending 생성
        response, _ = self.request_signup()  # 같은 아이디 재요청
        self.assertEqual(response.status_code, 400)

    def test_mail_failure_rolls_back_request(self):
        from unittest.mock import patch
        from api.models import SignupRequest
        with patch('api.auth_views.gmail_client.send_email', side_effect=Exception('smtp down')):
            response = self.client.post(
                '/api/auth/signup-requests/',
                {'username': 'newbie', 'password': 'newbie-pass-77!'},
                content_type='application/json')
        self.assertEqual(response.status_code, 502)
        self.assertFalse(SignupRequest.objects.filter(username='newbie').exists())


class CaseRelationTests(TestCase):
    """케이스 간 상호 참조 추가/해제."""

    def setUp(self):
        from django.contrib.auth.models import User
        from .permissions import set_user_role
        for username, role in (('rv1', 'viewer'), ('re1', 'engineer')):
            user = User.objects.create_user(username, password='rel-pass-123!')
            set_user_role(user, role)
        self.a = make_case(vendor='A10', summary='본 케이스입니다 다섯자이상')
        self.b = make_case(vendor='A10', summary='관련 케이스입니다 다섯자이상')

    def login(self, username):
        self.client.post('/api/auth/login/',
                         {'username': username, 'password': 'rel-pass-123!'},
                         content_type='application/json')

    def test_add_relation_by_display_number_is_symmetric(self):
        self.login('re1')
        response = self.client.post(f'/api/cases/{self.a.id}/relations/',
                                    {'case_id': f'C-{1000 + self.b.id}'},
                                    content_type='application/json')
        self.assertEqual(response.status_code, 201)
        # 양방향 반영 + 상세 응답에 포함
        detail_b = self.client.get(f'/api/cases/{self.b.id}/').json()
        self.assertEqual(detail_b['related_cases'][0]['id'], self.a.id)

    def test_remove_relation(self):
        self.login('re1')
        self.a.related_cases.add(self.b)
        response = self.client.delete(f'/api/cases/{self.a.id}/relations/{self.b.id}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.a.related_cases.count(), 0)

    def test_viewer_cannot_modify_relations(self):
        self.login('rv1')
        response = self.client.post(f'/api/cases/{self.a.id}/relations/',
                                    {'case_id': f'C-{1000 + self.b.id}'},
                                    content_type='application/json')
        self.assertEqual(response.status_code, 403)

    def test_invalid_ref_and_self_ref_rejected(self):
        self.login('re1')
        bad = self.client.post(f'/api/cases/{self.a.id}/relations/',
                               {'case_id': 'C-9999'}, content_type='application/json')
        self.assertEqual(bad.status_code, 400)
        own = self.client.post(f'/api/cases/{self.a.id}/relations/',
                               {'case_id': f'C-{1000 + self.a.id}'},
                               content_type='application/json')
        self.assertEqual(own.status_code, 400)
