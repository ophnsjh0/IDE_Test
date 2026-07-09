from django.core.management.base import BaseCommand

from api.services.gmail_sync import sync_gmail


class Command(BaseCommand):
    help = 'Sync vendor case emails from Gmail into Case-Flow'

    def add_arguments(self, parser):
        parser.add_argument('--max-results', type=int, default=50,
                            help='Maximum number of messages to fetch per run')

    def handle(self, *args, **options):
        summary = sync_gmail(max_results=options['max_results'])
        self.stdout.write(self.style.SUCCESS(
            f"fetched={summary['fetched']} cases_created={summary['cases_created']} "
            f"emails_added={summary['emails_added']} ignored={summary['ignored']} "
            f"no_vendor={summary['no_vendor']} skipped={summary['skipped']} "
            f"errors={summary['errors']}"
        ))
