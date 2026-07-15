"""번역 누락 메일(body_ko 빈값)만 골라 다시 번역하는 백필 커맨드.

무료 티어 429/503 등으로 분석이 실패하면 메일은 원문 폴백으로 저장된다.
이 커맨드는 그 누락분의 subject_ko/body_ko만 채우고 케이스 필드
(summary/status/action_steps 등)는 건드리지 않는다 — 전체를 재분석하며
케이스 필드를 리셋하는 reanalyze_cases보다 가볍고 수동 편집도 보존된다.
성공한 메일은 대상에서 빠지므로 여러 번 실행해도 안전하다.

    python manage.py backfill_translations                        # 실제 반영
    python manage.py backfill_translations --dry-run              # 대상만 출력
    python manage.py backfill_translations --model claude-haiku-4-5
    python manage.py backfill_translations --limit 5 --sleep 10
"""
import time
from contextlib import nullcontext

from django.core.management.base import BaseCommand

from api.models import CaseEmail
from api.services.analyzer import analyze_email, translation_model_override
from api.services.gmail_sync import build_case_context


class Command(BaseCommand):
    help = '번역이 비어 있는 메일만 다시 번역해 subject_ko/body_ko를 채운다'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='번역하지 않고 대상 목록만 출력')
        parser.add_argument('--limit', type=int, default=0,
                            help='처리할 최대 메일 수 (0=전체)')
        parser.add_argument('--model', type=str, default=None,
                            help='이번 실행에만 쓸 모델 (앱 설정은 변경하지 않음)')
        parser.add_argument('--sleep', type=float, default=5.0,
                            help='호출 간 대기 초 — 무료 티어 분당 한도 대응 (기본 5초)')
        parser.add_argument('--retries', type=int, default=2,
                            help='실패 시 재시도 횟수, 15초·30초 백오프 (기본 2회)')

    def handle(self, *args, **options):
        emails = (CaseEmail.objects
                  .filter(body_ko='')
                  .exclude(body_original='')
                  .select_related('case')
                  .order_by('case_id', 'received_at'))
        if options['limit']:
            emails = emails[:options['limit']]
        emails = list(emails)

        if not emails:
            self.stdout.write(self.style.SUCCESS('번역 누락 메일이 없습니다.'))
            return

        if options['dry_run']:
            for email in emails:
                self.stdout.write(
                    f'{email.case.case_id} #{email.id} [{email.direction}] {email.subject[:60]}')
            self.stdout.write(self.style.SUCCESS(f'(dry-run) 대상 {len(emails)}건'))
            return

        override = (translation_model_override(options['model'])
                    if options['model'] else nullcontext())
        done = failed = 0
        with override:
            for index, email in enumerate(emails):
                if index and options['sleep']:
                    time.sleep(options['sleep'])
                analysis = self._translate_with_retry(email, options['retries'])
                if not analysis or not (analysis.get('body_ko') or '').strip():
                    failed += 1
                    self.stderr.write(f'{email.case.case_id} #{email.id} 번역 실패')
                    continue
                # 메일의 번역 필드만 갱신 — 케이스 필드는 불변
                email.subject_ko = (analysis.get('subject_ko') or '')[:500]
                email.body_ko = analysis['body_ko']
                email.save(update_fields=['subject_ko', 'body_ko'])
                done += 1
                self.stdout.write(
                    f'{email.case.case_id} #{email.id} 완료 ({done + failed}/{len(emails)})')

        style = self.style.SUCCESS if failed == 0 else self.style.WARNING
        self.stdout.write(style(f'번역 완료 {done}건, 실패 {failed}건 / 대상 {len(emails)}건'))
        if failed:
            self.stdout.write('실패분은 같은 명령을 다시 실행하면 재시도됩니다. '
                              '(무료 티어 한도라면 --sleep 증가 또는 --model로 유료 모델 지정)')

    @staticmethod
    def _translate_with_retry(email, retries):
        """analyze_email은 실패 시 None을 반환하므로 백오프 후 재시도한다."""
        for attempt in range(retries + 1):
            if attempt:
                time.sleep(15 * attempt)  # 429(분당 한도) 해소 대기: 15초, 30초...
            analysis = analyze_email(
                subject=email.subject,
                body=email.body_original,
                direction=email.direction,
                is_new_case=False,
                case_context=build_case_context(email.case),
            )
            if analysis:
                return analysis
        return None
