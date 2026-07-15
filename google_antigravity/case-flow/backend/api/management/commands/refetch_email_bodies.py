"""기존 메일의 body_original을 개선된 HTML→텍스트 추출기로 다시 생성하는 백필.

HTML 메일을 인라인 태그마다 줄바꿈하던 옛 추출기 탓에 서명(명함)·표가
토큰 단위로 갈라져 저장됐다. Gmail API에서 원문을 다시 읽어(읽기 전용)
body_original만 갱신한다. 라벨·케이스·번역본에는 손대지 않으므로
여러 번 실행해도 안전하다.

    python manage.py refetch_email_bodies            # 실제 반영
    python manage.py refetch_email_bodies --dry-run  # 변경 미리보기
    python manage.py refetch_email_bodies --limit 5  # 일부만 처리(테스트)
"""
from django.core.management.base import BaseCommand
from googleapiclient.errors import HttpError

from api.models import CaseEmail
from api.services import gmail_client


class Command(BaseCommand):
    help = '저장된 메일 본문을 Gmail에서 다시 읽어 개선된 추출기로 재생성한다'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='DB에 저장하지 않고 변경될 건수만 출력')
        parser.add_argument('--limit', type=int, default=0,
                            help='처리할 최대 메일 수 (0=전체)')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']
        service = gmail_client.get_gmail_service()

        emails = CaseEmail.objects.select_related('case').order_by('id')
        if limit:
            emails = emails[:limit]

        updated = unchanged = failed = 0
        for email in emails:
            try:
                message = gmail_client.get_message(service, email.gmail_message_id)
            except HttpError as e:
                failed += 1
                self.stderr.write(f'{email.case.case_id} #{email.id} 조회 실패: {e}')
                continue
            new_body = gmail_client.extract_body(message.get('payload', {}))
            if not new_body or new_body == email.body_original:
                unchanged += 1
                continue
            updated += 1
            self.stdout.write(
                f'{email.case.case_id} #{email.id} '
                f'{len(email.body_original)} → {len(new_body)}자'
            )
            if not dry_run:
                email.body_original = new_body
                email.save(update_fields=['body_original'])

        mode = '(dry-run, 저장 안 함) ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(
            f'{mode}갱신 {updated}건, 변화 없음 {unchanged}건, 실패 {failed}건'
        ))
