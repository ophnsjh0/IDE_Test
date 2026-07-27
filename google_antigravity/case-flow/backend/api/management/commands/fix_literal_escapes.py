"""AI 번역 응답이 개행을 리터럴 백슬래시-n 등으로 남긴 body_ko를 되돌리는 백필.

일부 모델(Gemini 등)이 구조화 출력 JSON 문자열 안에 실제 개행 대신
리터럴 \\n(등)을 그대로 남기는 비결정적 오류가 있어(2026-07-27 발견),
analyzer._fix_literal_escapes가 이후 분석부터는 막아주지만 이미 저장된
body_ko는 그대로 남는다. AI 재호출 없이 텍스트 치환만 하므로 비용 없이
안전하게 반복 실행 가능하다.

    python manage.py fix_literal_escapes            # 실제 반영
    python manage.py fix_literal_escapes --dry-run   # 변경 미리보기
"""
from django.core.management.base import BaseCommand

from api.models import CaseEmail
from api.services.analyzer import _fix_literal_escapes


class Command(BaseCommand):
    help = 'body_ko에 남은 리터럴 이스케이프(\\n 등)를 실제 개행/탭으로 되돌린다'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='DB에 저장하지 않고 변경될 건수만 출력')

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        updated = 0
        for email in CaseEmail.objects.exclude(body_ko='').select_related('case'):
            fixed = _fix_literal_escapes(email.body_ko)
            if fixed == email.body_ko:
                continue
            updated += 1
            self.stdout.write(f'{email.case.case_id} #{email.id} 수정')
            if not dry_run:
                email.body_ko = fixed
                email.save(update_fields=['body_ko'])

        mode = '(dry-run, 저장 안 함) ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(f'{mode}수정 {updated}건'))
