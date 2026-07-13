import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import anthropic

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import AppSetting, Case, CaseEmail
from .services import help_agent
from .services.email_parser import (build_gmail_query, clean_subject,
                                    detect_vendor_and_direction,
                                    extract_device_info, normalize_body)
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

    def test_delete_account(self):
        from django.contrib.auth.models import User
        self.login('staff1', 'admin-pass-123!')
        normal = User.objects.get(username='normal1')
        response = self.client.delete(f'/api/auth/users/{normal.id}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['deleted'], 'normal1')
        self.assertFalse(User.objects.filter(username='normal1').exists())
        # 삭제된 계정은 로그인 불가
        self.client.post('/api/auth/logout/')
        self.assertEqual(self.login('normal1', 'normal-pass-123!').status_code, 401)

    def test_self_deletion_denied(self):
        from django.contrib.auth.models import User
        self.login('staff1', 'admin-pass-123!')
        staff = User.objects.get(username='staff1')
        response = self.client.delete(f'/api/auth/users/{staff.id}/')
        self.assertEqual(response.status_code, 400)
        self.assertTrue(User.objects.filter(username='staff1').exists())

    def test_normal_user_cannot_delete(self):
        from django.contrib.auth.models import User
        self.login('normal1', 'normal-pass-123!')
        staff = User.objects.get(username='staff1')
        response = self.client.delete(f'/api/auth/users/{staff.id}/')
        self.assertEqual(response.status_code, 403)


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


@override_settings(GROUP_VENDOR_HINTS={'adc@ubersys.co.kr': 'A10'},
                   GMAIL_SYNC_INCLUDE_SUBJECTS=['Caseopen'])
class CustomerThreadVendorTests(TestCase):
    """벤더 도메인이 없는 고객사↔당사 스레드([Caseopen])의 벤더 추정."""

    def test_customer_mail_with_group_cc_gets_hinted_vendor(self):
        vendor, direction = detect_vendor_and_direction(
            '"엄현식" <hyunsik.um@samsung.com>',
            '"성의제" <ujseong22@ubersys.co.kr>',
            cc='"위버시스템즈(A10)" <adc@ubersys.co.kr>, IaaS NW <iaas.nw@samsung.com>',
        )
        self.assertEqual((vendor, direction), ('A10', 'inbound'))

    def test_our_reply_to_customer_is_outbound(self):
        vendor, direction = detect_vendor_and_direction(
            '"성의제" <ujseong22@ubersys.co.kr>',
            'hyunsik.um@samsung.com',
            cc='"adc@ubersys.co.kr" <adc@ubersys.co.kr>',
        )
        self.assertEqual((vendor, direction), ('A10', 'outbound'))

    def test_vendor_domain_still_wins_over_group_hint(self):
        vendor, direction = detect_vendor_and_direction(
            'tac@arista.com',
            'eng@ubersys.co.kr',
            cc='adc@ubersys.co.kr',
        )
        self.assertEqual((vendor, direction), ('Arista', 'inbound'))

    def test_no_hint_and_no_vendor_domain_returns_none(self):
        vendor, direction = detect_vendor_and_direction(
            'someone@samsung.com', 'eng@ubersys.co.kr', cc='other@ubersys.co.kr')
        self.assertIsNone(vendor)

    def test_gmail_query_includes_subject_keywords(self):
        query = build_gmail_query()
        self.assertIn('subject:Caseopen', query)
        # OR 그룹({}) 안에 들어가야 벤더 도메인 조건과 합집합이 된다
        self.assertIn('subject:Caseopen', query.split('}')[0])


@override_settings(GMAIL_SYNC_INCLUDE_SUBJECTS=['Caseopen'])
class ExactSubjectMatchTests(TestCase):
    """스레드를 끊는 메일러(삼성 RE:(2) 카운터)의 케이스 오픈 스레드 병합."""

    SUBJECT = '[Caseopen] 수원 SCPv2 Multi-AZ 개발계 DATALB 파티션 변경 오류(API)'

    def test_clean_subject_strips_reply_counters(self):
        self.assertEqual(clean_subject(f'RE:(2) (2) {self.SUBJECT}'), self.SUBJECT)
        self.assertEqual(clean_subject(f'Re: (2) {self.SUBJECT}'), self.SUBJECT)

    def test_broken_thread_reply_matches_original_case(self):
        case = make_case(vendor='A10')
        make_email(case, self.SUBJECT, thread_id='thread-1')
        found = _find_case(None, 'thread-2', 'A10', f'RE:(2) (2) {self.SUBJECT}')
        self.assertEqual(found, case)

    def test_subject_without_open_keyword_is_not_merged(self):
        case = make_case(vendor='A10')
        make_email(case, '수원 SCPv2 개발계 정기 점검 안내', thread_id='thread-1')
        found = _find_case(None, 'thread-2', 'A10', '수원 SCPv2 개발계 정기 점검 안내')
        self.assertIsNone(found)

    def test_other_vendor_same_subject_is_not_merged(self):
        case = make_case(vendor='Arista')
        make_email(case, self.SUBJECT, thread_id='thread-1')
        found = _find_case(None, 'thread-2', 'A10', f'Re: {self.SUBJECT}')
        self.assertIsNone(found)


class HelpAgentToolTests(TestCase):
    """헬프 에이전트 DB 조회 도구의 동작 검증 (LLM 호출 없음)."""

    def setUp(self):
        self.case = make_case(
            vendor='A10', status='Resolved',
            summary='수원 SCPv2 DATALB 파티션 변경 오류',
            device_model='TH1040-F',
        )
        make_email(self.case, '[Caseopen] 수원 SCPv2 DATALB 파티션 변경 오류')
        make_case(vendor='Arista', summary='40G Interface Link FLAP')

    def test_search_by_keyword_and_vendor(self):
        data = json.loads(help_agent._search_cases(query='파티션', vendor='A10'))
        self.assertEqual(len(data['results']), 1)
        self.assertEqual(data['results'][0]['case_id'], self.case.case_id)

    def test_search_by_case_ref(self):
        data = json.loads(help_agent._search_cases(query=self.case.case_id))
        self.assertEqual(data['results'][0]['case_id'], self.case.case_id)

    def test_detail_resolves_c_format_and_includes_emails(self):
        data = json.loads(help_agent._get_case_detail(self.case.case_id))
        self.assertEqual(data['case_id'], self.case.case_id)
        self.assertEqual(len(data['emails']), 1)

    def test_detail_unknown_case_returns_error(self):
        data = json.loads(help_agent._get_case_detail('C-9999'))
        self.assertIn('error', data)

    def test_stats_counts_by_vendor(self):
        data = json.loads(help_agent._get_case_stats(days=7))
        self.assertEqual(data['total'], 2)
        self.assertEqual(data['by_vendor']['A10'], 1)

    def test_verify_flags_hallucinated_case_ref(self):
        reply = help_agent._verify_case_refs(
            f'{self.case.case_id} 및 C-8888 참조')
        self.assertIn('C-8888', reply)
        self.assertIn('확인되지 않았습니다', reply)

    def test_verify_passes_valid_refs_untouched(self):
        reply = help_agent._verify_case_refs(f'{self.case.case_id} 참조')
        self.assertNotIn('확인되지', reply)

    def test_list_recent_cases_marks_new_and_filters_vendor(self):
        data = json.loads(help_agent._list_recent_cases(days=7, vendor='A10'))
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['case_id'], self.case.case_id)
        self.assertTrue(data['results'][0]['is_new'])


class HelpAgentTriageTests(TestCase):
    """트리아지: 규칙 우선, 애매하면 haiku 분류, 실패 시 search 폴백."""

    def test_report_keyword_skips_llm(self):
        client = MagicMock()
        agent = help_agent._triage(
            client, [{'role': 'user', 'content': '이번 주 케이스 리포트 만들어줘'}])
        self.assertEqual(agent, 'report')
        client.messages.create.assert_not_called()

    def test_ambiguous_question_uses_llm_classifier(self):
        client = MagicMock()
        client.messages.create.return_value = SimpleNamespace(
            content=[_fake_block(type='text', text='report')])
        agent = help_agent._triage(
            client, [{'role': 'user', 'content': '요즘 케이스들 어떻게 돌아가?'}])
        self.assertEqual(agent, 'report')
        client.messages.create.assert_called_once()

    def test_classifier_failure_falls_back_to_search(self):
        client = MagicMock()
        client.messages.create.side_effect = anthropic.APIConnectionError(
            request=MagicMock())
        agent = help_agent._triage(
            client, [{'role': 'user', 'content': 'C-1122 상태 알려줘'}])
        self.assertEqual(agent, 'search')

    def test_off_topic_classification(self):
        client = MagicMock()
        client.messages.create.return_value = SimpleNamespace(
            content=[_fake_block(type='text', text='off_topic')])
        agent = help_agent._triage(
            client, [{'role': 'user', 'content': '오늘 저녁 뭐 먹을까?'}])
        self.assertEqual(agent, 'off_topic')

    def test_followup_question_includes_conversation_context(self):
        # "인터넷에서 더 찾아줘" 같은 후속 질문은 직전 맥락과 함께 분류돼야 함
        client = MagicMock()
        client.messages.create.return_value = SimpleNamespace(
            content=[_fake_block(type='text', text='tech')])
        agent = help_agent._triage(client, [
            {'role': 'user', 'content': 'C-1122 VRRP 버그 상태 알려줘'},
            {'role': 'assistant', 'content': 'C-1122는 Resolved 상태입니다.'},
            {'role': 'user', 'content': '인터넷에서 상세 검색해줘'},
        ])
        self.assertEqual(agent, 'tech')
        sent = client.messages.create.call_args.kwargs['messages'][0]['content']
        self.assertIn('이전 대화 맥락', sent)
        self.assertIn('VRRP 버그', sent)
        self.assertIn('인터넷에서 상세 검색해줘', sent)

    @override_settings(ANTHROPIC_API_KEY='test-key', HELP_AGENT_MODEL='claude-haiku-4-5')
    def test_off_topic_short_circuits_without_agent_call(self):
        fake_client = MagicMock()
        fake_client.messages.create.return_value = SimpleNamespace(
            content=[_fake_block(type='text', text='off_topic')])
        with patch.object(help_agent.anthropic, 'Anthropic', return_value=fake_client):
            result = help_agent.chat([{'role': 'user', 'content': '주식 추천해줘'}])

        self.assertEqual(result['agent'], 'off_topic')
        self.assertEqual(result['tool_calls'], [])
        self.assertIn('범위 밖', result['reply'])
        # 트리아지 1회만 호출 — 에이전트 본 호출 없음 (비용 가드)
        self.assertEqual(fake_client.messages.create.call_count, 1)


def _fake_block(**kwargs):
    return SimpleNamespace(**kwargs)


class HelpAgentChatLoopTests(TestCase):
    """에이전트 루프: 도구 호출 → 결과 회신 → 최종 답변 (Anthropic 모킹)."""

    def setUp(self):
        self.case = make_case(vendor='A10', summary='VRRP failover 장애')

    @override_settings(ANTHROPIC_API_KEY='test-key', HELP_AGENT_MODEL='claude-haiku-4-5')
    def test_tool_loop_returns_final_reply_and_trace(self):
        triage_turn = SimpleNamespace(
            stop_reason='end_turn',
            content=[_fake_block(type='text', text='search')],
        )
        tool_turn = SimpleNamespace(
            stop_reason='tool_use',
            content=[_fake_block(type='tool_use', id='tu_1', name='search_cases',
                                 input={'query': 'VRRP'})],
        )
        final_turn = SimpleNamespace(
            stop_reason='end_turn',
            content=[_fake_block(type='text',
                                 text=f'{self.case.case_id} 케이스가 있습니다.')],
        )
        fake_client = MagicMock()
        fake_client.messages.create.side_effect = [triage_turn, tool_turn, final_turn]

        with patch.object(help_agent.anthropic, 'Anthropic', return_value=fake_client):
            result = help_agent.chat([{'role': 'user', 'content': 'VRRP 장애 사례 찾아줘'}])

        self.assertEqual(result['agent'], 'search')
        self.assertIn(self.case.case_id, result['reply'])
        self.assertEqual(result['tool_calls'], [{'name': 'search_cases',
                                                 'input': {'query': 'VRRP'}}])
        # 3번째 호출(도구 회신 후)의 messages에 tool_result가 포함됐는지 확인
        third_call_messages = fake_client.messages.create.call_args_list[2].kwargs['messages']
        self.assertEqual(third_call_messages[-1]['content'][0]['type'], 'tool_result')

    @override_settings(ANTHROPIC_API_KEY='test-key',
                       HELP_AGENT_MODEL='claude-haiku-4-5',
                       REPORT_AGENT_MODEL='claude-sonnet-5')
    def test_report_request_routes_to_report_agent(self):
        final_turn = SimpleNamespace(
            stop_reason='end_turn',
            content=[_fake_block(type='text', text='## 주간 리포트\n요약입니다.')],
        )
        fake_client = MagicMock()
        # 리포팅은 문서 스킬 때문에 beta 엔드포인트를 사용한다
        fake_client.beta.messages.create.return_value = final_turn

        with patch.object(help_agent.anthropic, 'Anthropic', return_value=fake_client):
            result = help_agent.chat(
                [{'role': 'user', 'content': '이번 주 케이스 리포트 작성해줘'}])

        self.assertEqual(result['agent'], 'report')
        self.assertEqual(result['model'], 'claude-sonnet-5')
        self.assertNotIn('files', result)  # 문서를 안 만들면 files 없음
        # 리포트 키워드는 규칙 분기 → 트리아지 LLM 호출 없이 본 호출 1회만
        fake_client.messages.create.assert_not_called()
        call = fake_client.beta.messages.create.call_args_list[0]
        self.assertEqual(call.kwargs['model'], 'claude-sonnet-5')
        tool_names = [t['name'] for t in call.kwargs['tools']]
        self.assertIn('list_recent_cases', tool_names)
        # 문서 스킬 구성: code_execution 도구 + 스킬 컨테이너 + beta 헤더
        self.assertIn('code_execution', tool_names)
        skill_ids = [s['skill_id'] for s in call.kwargs['container']['skills']]
        self.assertEqual(skill_ids, ['docx', 'xlsx', 'pptx'])
        self.assertEqual(call.kwargs['betas'],
                         ['code-execution-2025-08-25', 'skills-2025-10-02'])

    @override_settings(ANTHROPIC_API_KEY='test-key',
                       HELP_AGENT_MODEL='claude-haiku-4-5',
                       REPORT_AGENT_MODEL='claude-sonnet-5')
    def test_report_collects_generated_files(self):
        # 코드 실행 결과 블록 안에 중첩된 file_id를 수집하는지
        file_ref = _fake_block(type='bash_code_execution_output',
                               file_id='file_abc123')
        exec_result = _fake_block(
            type='bash_code_execution_tool_result',
            content=_fake_block(type='bash_code_execution_result',
                                content=[file_ref]),
        )
        final_turn = SimpleNamespace(
            stop_reason='end_turn',
            content=[exec_result,
                     _fake_block(type='text', text='엑셀 리포트를 만들었습니다.')],
        )
        fake_client = MagicMock()
        fake_client.beta.messages.create.return_value = final_turn
        fake_client.beta.files.retrieve_metadata.return_value = SimpleNamespace(
            filename='caseflow_report.xlsx', size_bytes=2048)

        with patch.object(help_agent.anthropic, 'Anthropic', return_value=fake_client):
            result = help_agent.chat(
                [{'role': 'user', 'content': '이번 주 리포트를 엑셀로 작성해줘'}])

        self.assertEqual(result['files'], [{
            'file_id': 'file_abc123',
            'filename': 'caseflow_report.xlsx',
            'size_bytes': 2048,
        }])

    def test_describe_files_filters_non_documents_and_dedupes(self):
        fake_client = MagicMock()
        fake_client.beta.files.retrieve_metadata.side_effect = [
            SimpleNamespace(filename='report.docx', size_bytes=100),
            SimpleNamespace(filename='build_report.py', size_bytes=50),
        ]
        files = help_agent._describe_files(
            fake_client, ['file_doc', 'file_doc', 'file_script'])
        self.assertEqual([f['filename'] for f in files], ['report.docx'])
        # 중복 file_id는 메타데이터 조회도 1회만
        self.assertEqual(fake_client.beta.files.retrieve_metadata.call_count, 2)

    @override_settings(ANTHROPIC_API_KEY='')
    def test_missing_api_key_raises(self):
        with self.assertRaises(RuntimeError):
            help_agent.chat([{'role': 'user', 'content': '안녕'}])


class HelpAgentTemplateTests(TestCase):
    """사내 템플릿 모드 — 워딩 트리거, 파일 첨부, 해시 캐싱 (Anthropic 모킹)."""

    def setUp(self):
        # 실제 템플릿 파일은 gitignore 대상이라 테스트는 임시 파일로 대체한다
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        docx = Path(self.tmpdir.name) / 'demo.docx'
        docx.write_bytes(b'docx-template-v1')
        self.docx_patch = patch.dict(
            help_agent.REPORT_TEMPLATES['docx'], {'path': docx})
        self.docx_patch.start()
        self.addCleanup(self.docx_patch.stop)

    def test_match_template_requires_both_keywords(self):
        # '템플릿' + 형식 단어가 함께 있을 때만 반응 (일반 파일 생성과 구분)
        self.assertEqual(
            help_agent._match_template('사내보고서 워드 템플릿으로 작성해줘'), 'docx')
        self.assertEqual(
            help_agent._match_template('C-1122 PPT 템플릿으로 정리해줘'), 'pptx')
        self.assertIsNone(help_agent._match_template('이번 주 리포트를 워드로 작성해줘'))
        self.assertIsNone(help_agent._match_template('템플릿이 뭐야?'))

    def test_template_wording_routes_to_report_without_llm_triage(self):
        # "…템플릿으로 만들어줘"는 보고서 단어가 없어도 규칙 분기로 report에 가야 함
        self.assertIn('템플릿', help_agent.REPORT_KEYWORDS)

    @override_settings(ANTHROPIC_API_KEY='test-key',
                       HELP_AGENT_MODEL='claude-haiku-4-5',
                       REPORT_AGENT_MODEL='claude-sonnet-5')
    def test_template_request_attaches_file_and_addendum(self):
        final_turn = SimpleNamespace(
            stop_reason='end_turn',
            content=[_fake_block(type='text', text='템플릿 보고서를 만들었습니다.')])
        fake_client = MagicMock()
        fake_client.beta.messages.create.return_value = final_turn
        fake_client.beta.files.upload.return_value = SimpleNamespace(id='file_tpl_1')

        with patch.object(help_agent.anthropic, 'Anthropic', return_value=fake_client):
            help_agent.chat(
                [{'role': 'user', 'content': 'C-1122 사내보고서 워드 템플릿으로 작성해줘'}])

        call = fake_client.beta.messages.create.call_args_list[0]
        last_content = call.kwargs['messages'][-1]['content']
        self.assertEqual(last_content[0],
                         {'type': 'container_upload', 'file_id': 'file_tpl_1'})
        self.assertIn('워드 템플릿으로 작성해줘', last_content[1]['text'])
        self.assertIn('사내 템플릿 모드', call.kwargs['system'])
        # 해시:file_id 캐시 저장 확인
        self.assertTrue(AppSetting.get('report_template_docx').endswith(':file_tpl_1'))

    @override_settings(ANTHROPIC_API_KEY='test-key',
                       HELP_AGENT_MODEL='claude-haiku-4-5',
                       REPORT_AGENT_MODEL='claude-sonnet-5')
    def test_plain_report_request_does_not_attach_template(self):
        final_turn = SimpleNamespace(
            stop_reason='end_turn',
            content=[_fake_block(type='text', text='## 주간 리포트')])
        fake_client = MagicMock()
        fake_client.beta.messages.create.return_value = final_turn

        with patch.object(help_agent.anthropic, 'Anthropic', return_value=fake_client):
            help_agent.chat([{'role': 'user', 'content': '이번 주 리포트 작성해줘'}])

        fake_client.beta.files.upload.assert_not_called()
        call = fake_client.beta.messages.create.call_args_list[0]
        self.assertIsInstance(call.kwargs['messages'][-1]['content'], str)
        self.assertNotIn('사내 템플릿 모드', call.kwargs['system'])

    def test_file_id_cached_until_template_file_changes(self):
        fake_client = MagicMock()
        fake_client.beta.files.upload.side_effect = [
            SimpleNamespace(id='file_v1'), SimpleNamespace(id='file_v2')]

        self.assertEqual(help_agent._template_file_id(fake_client, 'docx'), 'file_v1')
        # 같은 파일이면 재업로드 없이 캐시 사용
        self.assertEqual(help_agent._template_file_id(fake_client, 'docx'), 'file_v1')
        self.assertEqual(fake_client.beta.files.upload.call_count, 1)

        # 파일 교체(해시 변경) → 재업로드 + 옛 파일 삭제
        help_agent.REPORT_TEMPLATES['docx']['path'].write_bytes(b'docx-template-v2')
        self.assertEqual(help_agent._template_file_id(fake_client, 'docx'), 'file_v2')
        fake_client.beta.files.delete.assert_called_once_with('file_v1')

    @override_settings(ANTHROPIC_API_KEY='test-key',
                       HELP_AGENT_MODEL='claude-haiku-4-5',
                       REPORT_AGENT_MODEL='claude-sonnet-5')
    def test_upload_failure_falls_back_to_plain_report(self):
        # 템플릿 첨부 실패는 500 대신 일반 리포트로 진행 (시연 중단 방지)
        final_turn = SimpleNamespace(
            stop_reason='end_turn',
            content=[_fake_block(type='text', text='일반 보고서입니다.')])
        fake_client = MagicMock()
        fake_client.beta.messages.create.return_value = final_turn
        fake_client.beta.files.upload.side_effect = anthropic.APIConnectionError(
            request=MagicMock())

        with patch.object(help_agent.anthropic, 'Anthropic', return_value=fake_client):
            result = help_agent.chat(
                [{'role': 'user', 'content': '워드 템플릿으로 보고서 작성해줘'}])

        self.assertEqual(result['reply'], '일반 보고서입니다.')
        call = fake_client.beta.messages.create.call_args_list[0]
        self.assertNotIn('사내 템플릿 모드', call.kwargs['system'])


class HelpAgentEndpointTests(TestCase):
    """POST /api/help-agent/chat/ 의 인증·검증·응답.

    AI 호출 비용 때문에 테스트 배포 동안 관리자 전용 (2026-07-11 결정).
    """

    def setUp(self):
        User.objects.create_user('viewer1', password='pw123456')
        User.objects.create_user('admin1', password='pw123456', is_staff=True)

    def login(self, username):
        self.client.post('/api/auth/login/',
                         {'username': username, 'password': 'pw123456'},
                         content_type='application/json')

    def test_requires_login(self):
        res = self.client.post('/api/help-agent/chat/',
                               {'messages': [{'role': 'user', 'content': '안녕'}]},
                               content_type='application/json')
        self.assertIn(res.status_code, (401, 403))

    def test_non_admin_is_blocked(self):
        self.login('viewer1')
        res = self.client.post('/api/help-agent/chat/',
                               {'messages': [{'role': 'user', 'content': '안녕'}]},
                               content_type='application/json')
        self.assertEqual(res.status_code, 403)

    def test_invalid_payload_rejected(self):
        self.login('admin1')
        res = self.client.post('/api/help-agent/chat/', {'messages': []},
                               content_type='application/json')
        self.assertEqual(res.status_code, 400)
        res = self.client.post(
            '/api/help-agent/chat/',
            {'messages': [{'role': 'assistant', 'content': '내가 마지막'}]},
            content_type='application/json')
        self.assertEqual(res.status_code, 400)

    def test_admin_can_chat(self):
        self.login('admin1')
        with patch('api.views.help_agent.chat',
                   return_value={'reply': '안녕하세요', 'tool_calls': [], 'model': 'm'}):
            res = self.client.post(
                '/api/help-agent/chat/',
                {'messages': [{'role': 'user', 'content': '안녕'}]},
                content_type='application/json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['reply'], '안녕하세요')

    def test_file_download_requires_admin(self):
        self.login('viewer1')
        res = self.client.get('/api/help-agent/files/file_abc123/')
        self.assertEqual(res.status_code, 403)

    def test_file_download_rejects_invalid_id(self):
        self.login('admin1')
        res = self.client.get('/api/help-agent/files/not-a-file-id/')
        self.assertEqual(res.status_code, 400)

    def test_file_download_relays_content(self):
        self.login('admin1')
        with patch('api.views.help_agent.download_file',
                   return_value=(
                       '리포트.xlsx',
                       'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                       b'excel-bytes')):
            res = self.client.get('/api/help-agent/files/file_abc123/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, b'excel-bytes')
        self.assertIn("filename*=UTF-8''%EB%A6%AC%ED%8F%AC%ED%8A%B8.xlsx",
                      res['Content-Disposition'])


@override_settings(SEARCH_BLOCKED_TERMS=['samsung', '삼성', '하나은행'])
class SearchQuerySanitizerTests(TestCase):
    """웹 검색어 보안 정제 — 고객사명·시리얼·사설 IP 제거 (코드 가드레일)."""

    def test_customer_names_removed(self):
        clean, removed = help_agent._sanitize_search_query(
            '삼성 SCP 환경 A10 파티션 변경 오류')
        self.assertNotIn('삼성', clean)
        self.assertIn('A10 파티션 변경 오류', clean)
        self.assertIn('삼성', removed)

    def test_private_ip_and_serial_removed(self):
        clean, removed = help_agent._sanitize_search_query(
            'TH1040-F 10.20.30.40 TH10154022070160 failover 원인')
        self.assertNotIn('10.20.30.40', clean)
        self.assertNotIn('TH10154022070160', clean)
        self.assertIn('TH1040-F', clean)  # 모델명은 유지
        self.assertIn('failover', clean)
        self.assertEqual(len(removed), 2)

    def test_clean_technical_query_untouched(self):
        clean, removed = help_agent._sanitize_search_query(
            'Arista EOS 4.32.4M PhyEthtool log advisory')
        self.assertEqual(clean, 'Arista EOS 4.32.4M PhyEthtool log advisory')
        self.assertEqual(removed, [])

    def test_vendor_bug_id_is_kept(self):
        # ACOS-104904 같은 버그 ID는 시리얼이 아니다 — 검색에 필요 (오탐 회귀 방지)
        clean, removed = help_agent._sanitize_search_query(
            'A10 ACOS-104904 VRRP-A advertisement timer bug')
        self.assertIn('ACOS-104904', clean)
        self.assertEqual(removed, [])


class WebSearchToolTests(TestCase):
    """web_search 도구 — Serper 연동(모킹)과 키 미설정 처리."""

    @override_settings(SERPER_API_KEY='')
    def test_missing_key_returns_error(self):
        data = json.loads(help_agent._web_search('EOS bug'))
        self.assertIn('error', data)

    @override_settings(SERPER_API_KEY='k', SEARCH_BLOCKED_TERMS=['삼성'])
    def test_results_parsed_and_sanitize_notice(self):
        fake_response = MagicMock()
        fake_response.json.return_value = {'organic': [
            {'title': 'ACOS Release Notes', 'link': 'https://a10.com/rn',
             'snippet': '6.0.9 fixes'},
        ]}
        with patch.object(help_agent.httpx, 'post',
                          return_value=fake_response) as post:
            data = json.loads(help_agent._web_search('삼성 ACOS 6.0.8 bug'))

        self.assertEqual(data['results'][0]['url'], 'https://a10.com/rn')
        self.assertIn('제거됨', data['notice'])
        # 실제 전송된 검색어에 고객사명이 없어야 함
        sent_query = post.call_args.kwargs['json']['q']
        self.assertNotIn('삼성', sent_query)

    @override_settings(SERPER_API_KEY='k', SEARCH_BLOCKED_TERMS=['삼성'])
    def test_fully_blocked_query_returns_error_without_request(self):
        with patch.object(help_agent.httpx, 'post') as post:
            data = json.loads(help_agent._web_search('삼성'))
        self.assertIn('error', data)
        post.assert_not_called()


class TechAgentFlowTests(TestCase):
    """② 기술지원: 트리아지 → sonnet 답변 → haiku 검수 → (미흡 시) 수정."""

    def _triage_resp(self, label):
        return SimpleNamespace(stop_reason='end_turn',
                               content=[_fake_block(type='text', text=label)])

    def _text_resp(self, text, stop_reason='end_turn'):
        return SimpleNamespace(stop_reason=stop_reason,
                               content=[_fake_block(type='text', text=text)])

    @override_settings(ANTHROPIC_API_KEY='test-key',
                       HELP_AGENT_MODEL='claude-haiku-4-5',
                       TECH_AGENT_MODEL='claude-sonnet-5')
    def test_evaluator_pass_returns_reply_without_revision(self):
        fake_client = MagicMock()
        fake_client.messages.create.side_effect = [
            self._triage_resp('tech'),
            self._text_resp('ACOS 6.0.9에서 수정되었습니다. [RN](https://a10.com/rn)'),
            self._text_resp('{"ok": true}'),  # 평가자
        ]
        with patch.object(help_agent.anthropic, 'Anthropic', return_value=fake_client):
            result = help_agent.chat(
                [{'role': 'user', 'content': 'ACOS 6.0.8 VRRP 버그 수정 버전 알려줘'}])

        self.assertEqual(result['agent'], 'tech')
        self.assertEqual(result['model'], 'claude-sonnet-5')
        self.assertTrue(result['evaluation']['ok'])
        self.assertIn('6.0.9', result['reply'])
        self.assertEqual(fake_client.messages.create.call_count, 3)  # 수정 라운드 없음

    @override_settings(ANTHROPIC_API_KEY='test-key',
                       HELP_AGENT_MODEL='claude-haiku-4-5',
                       TECH_AGENT_MODEL='claude-sonnet-5')
    def test_evaluator_fail_triggers_one_revision(self):
        fake_client = MagicMock()
        fake_client.messages.create.side_effect = [
            self._triage_resp('tech'),
            self._text_resp('근거 없는 초안'),
            self._text_resp('{"ok": false, "issues": ["출처 인용 없음"]}'),  # 평가자
            self._text_resp('수정된 답변 [출처](https://vendor.com/doc)'),   # 수정 라운드
        ]
        with patch.object(help_agent.anthropic, 'Anthropic', return_value=fake_client):
            result = help_agent.chat(
                [{'role': 'user', 'content': 'EOS 업그레이드 시 주의사항 알려줘'}])

        self.assertFalse(result['evaluation']['ok'])
        self.assertIn('수정된 답변', result['reply'])
        self.assertEqual(fake_client.messages.create.call_count, 4)
        # 수정 요청에 검수 피드백이 전달됐는지 확인
        revision_messages = fake_client.messages.create.call_args_list[3].kwargs['messages']
        self.assertIn('자동 검수 피드백', revision_messages[-1]['content'])

    @override_settings(ANTHROPIC_API_KEY='test-key',
                       HELP_AGENT_MODEL='claude-haiku-4-5',
                       TECH_AGENT_MODEL='claude-sonnet-5')
    def test_handoff_reroutes_to_target_agent_once(self):
        # 검색 에이전트에게 웹 검색 요청이 잘못 배정 → [HANDOFF:tech] → 재배정
        fake_client = MagicMock()
        fake_client.messages.create.side_effect = [
            self._triage_resp('search'),                 # 트리아지 오분류
            self._text_resp('[HANDOFF:tech]'),           # 검색 에이전트가 핸드오프
            self._text_resp('EOS 4.32 관련 자료입니다. [출처](https://arista.com)'),
            self._text_resp('{"ok": true}'),             # 평가자
        ]
        with patch.object(help_agent.anthropic, 'Anthropic', return_value=fake_client):
            result = help_agent.chat(
                [{'role': 'user', 'content': '인터넷에서 상세 검색해줘'}])

        self.assertEqual(result['agent'], 'tech')
        self.assertEqual(result['model'], 'claude-sonnet-5')
        self.assertIn('EOS 4.32', result['reply'])
        self.assertNotIn('HANDOFF', result['reply'])
        # 재배정된 tech 호출이 tech 프롬프트로 나갔는지 확인
        tech_call = fake_client.messages.create.call_args_list[2]
        self.assertEqual(tech_call.kwargs['model'], 'claude-sonnet-5')

    @override_settings(ANTHROPIC_API_KEY='test-key',
                       HELP_AGENT_MODEL='claude-haiku-4-5',
                       TECH_AGENT_MODEL='claude-sonnet-5')
    def test_handoff_to_same_agent_is_ignored(self):
        # 자기 자신으로의 핸드오프는 재배정하지 않고 마커만 제거 (루프 방지)
        fake_client = MagicMock()
        fake_client.messages.create.side_effect = [
            self._triage_resp('search'),
            self._text_resp('[HANDOFF:search]'),
        ]
        with patch.object(help_agent.anthropic, 'Anthropic', return_value=fake_client):
            result = help_agent.chat([{'role': 'user', 'content': '케이스 찾아줘'}])

        self.assertEqual(result['agent'], 'search')
        self.assertNotIn('HANDOFF', result['reply'])
        self.assertEqual(fake_client.messages.create.call_count, 2)


class GmailSyncConcurrencyTests(TestCase):
    """동기화 동시 실행 잠금과 저장 직전 중복(경쟁 상태) 방어."""

    def test_concurrent_sync_rejected_by_lock(self):
        import fcntl
        from .services import gmail_sync

        # 다른 동기화가 실행 중인 상태를 재현: 잠금을 직접 잡아둔다
        holder = open(gmail_sync._LOCK_FILE, 'w')
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with self.assertRaises(gmail_sync.SyncInProgress):
                gmail_sync.sync_gmail()
        finally:
            fcntl.flock(holder, fcntl.LOCK_UN)
            holder.close()

        # 잠금 해제 후에는 정상 진입 (Gmail 호출은 mock)
        with patch.object(gmail_sync, '_sync_gmail', return_value={'fetched': 0}):
            self.assertEqual(gmail_sync.sync_gmail(), {'fetched': 0})

    def test_duplicate_at_save_time_rolls_back_new_case(self):
        """중복 체크 통과 후 다른 동기화가 먼저 저장한 경우:
        skipped 처리되고, 이 실행이 만들던 새 케이스도 롤백되어야 한다."""
        from .services import gmail_sync

        other_case = make_case(vendor='Arista')

        message = {
            'id': 'race-msg-1',
            'threadId': 'race-thread-1',
            'internalDate': '0',
            'payload': {'headers': [
                {'name': 'From', 'value': 'Arista Support <support@arista.com>'},
                {'name': 'To', 'value': 'adc@ubersys.co.kr'},
                {'name': 'Subject', 'value': 'New Case: SR 77001 something broken'},
                {'name': 'Date', 'value': 'Mon, 13 Jul 2026 10:00:00 +0900'},
            ]},
        }

        # AI 분석이 도는 사이에 다른 동기화가 같은 메일을 먼저 저장하는 상황
        def analyze_and_race(**kwargs):
            make_email(other_case, 'raced', message_id='race-msg-1',
                       thread_id='race-thread-1')
            return None

        cases_before = Case.objects.count()
        with patch.object(gmail_sync, 'analyze_email', side_effect=analyze_and_race):
            result = gmail_sync._process_message(message)

        self.assertEqual(result, 'skipped')
        # 이메일은 먼저 저장된 1건만 존재
        self.assertEqual(
            CaseEmail.objects.filter(gmail_message_id='race-msg-1').count(), 1)
        # 이 실행이 만들던 새 케이스는 롤백되어 빈 케이스가 남지 않는다
        self.assertEqual(Case.objects.count(), cases_before)
