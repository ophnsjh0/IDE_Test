from django.core.management.base import BaseCommand

from api.services.analyzer import translation_model_override
from api.services.gmail_sync import SyncInProgress, sync_gmail


class Command(BaseCommand):
    help = 'Sync vendor case emails from Gmail into Case-Flow'

    def add_arguments(self, parser):
        parser.add_argument('--max-results', type=int, default=50,
                            help='Maximum number of messages to fetch per run')
        parser.add_argument('--model',
                            help='이번 실행에만 사용할 분석 모델 (앱 설정은 그대로 둔다). '
                                 '실패 시 settings.TRANSLATION_FALLBACK_MODELS로 재시도')

    def handle(self, *args, **options):
        try:
            with translation_model_override(options['model']):
                summary = sync_gmail(max_results=options['max_results'])
        except SyncInProgress as exc:
            # cron과 웹 버튼이 겹칠 수 있다 — 정상 종료로 처리해 cron 메일을 만들지 않는다
            self.stdout.write(self.style.WARNING(str(exc)))
            return
        self.stdout.write(self.style.SUCCESS(
            f"fetched={summary['fetched']} cases_created={summary['cases_created']} "
            f"emails_added={summary['emails_added']} ignored={summary['ignored']} "
            f"no_vendor={summary['no_vendor']} skipped={summary['skipped']} "
            f"errors={summary['errors']}"
        ))
