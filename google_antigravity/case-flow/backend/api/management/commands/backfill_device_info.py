"""기존 케이스의 이메일에서 장비 모델/시리얼/버전을 추출해 채우는 백필 커맨드.

정규식(고신뢰 패턴)만 사용하므로 AI API 호출 비용/쿼터 부담이 없다.
빈 필드만 채우므로 여러 번 실행해도 안전하다.

    python manage.py backfill_device_info          # 실제 반영
    python manage.py backfill_device_info --dry-run  # 결과 미리보기만
"""
from django.core.management.base import BaseCommand

from api.models import Case
from api.services import email_parser
from api.services.gmail_sync import DEVICE_INFO_FIELDS


class Command(BaseCommand):
    help = '기존 케이스 이메일에서 장비 모델/시리얼/버전을 정규식으로 추출해 빈 필드를 채운다'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='DB에 저장하지 않고 추출 결과만 출력')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        filled = dict.fromkeys(DEVICE_INFO_FIELDS, 0)
        cases_changed = 0

        for case in Case.objects.prefetch_related('emails'):
            changed_fields = []
            # 오픈 메일(가장 오래된 메일)부터 훑어 먼저 찾은 값을 채택
            for email in case.emails.order_by('received_at'):
                info = email_parser.extract_device_info(email.subject, email.body_original)
                for field in DEVICE_INFO_FIELDS:
                    value = info.get(field)
                    if value and not getattr(case, field):
                        max_length = Case._meta.get_field(field).max_length
                        setattr(case, field, value[:max_length])
                        filled[field] += 1
                        changed_fields.append(field)
                if all(getattr(case, f) for f in DEVICE_INFO_FIELDS):
                    break

            if changed_fields:
                cases_changed += 1
                self.stdout.write(
                    f'{case.case_id} [{case.vendor}] '
                    + ', '.join(f'{f}={getattr(case, f)}' for f in changed_fields)
                )
                if not dry_run:
                    case.save(update_fields=changed_fields + ['updated_at'])

        mode = '(dry-run, 저장 안 함) ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(
            f'{mode}케이스 {cases_changed}건 갱신 — '
            + ', '.join(f'{f}: {n}건' for f, n in filled.items())
        ))
