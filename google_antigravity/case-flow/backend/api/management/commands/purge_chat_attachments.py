"""Anthropic Files API에 남은 고아 파일을 정리한다 (기본은 조회만).

    python manage.py purge_chat_attachments              # 삭제 대상만 출력
    python manage.py purge_chat_attachments --apply      # 실제 삭제
    python manage.py purge_chat_attachments --days 7 --apply

첨부는 업로드 직후 file_id만 프론트에 돌려주므로, 사용자가 파일을 고른 뒤
질문을 보내지 않고 창을 닫으면 어디에도 참조되지 않은 파일이 남는다.
고객사 자료가 올라올 수 있어 스토리지에 방치하면 안 되고, 대화를 지울 때
지우는 경로(views.ChatSessionDetailView.delete)만으로는 이 경우를 못 잡는다.

지우면 안 되는 파일이 같은 스토리지에 섞여 있어, 다음 참조를 모아 보존한다:
  - ChatTurn.attachments — 대화에 실제로 실린 사용자 첨부
  - ChatTurn.files       — 리포팅 에이전트가 만든 문서 (다운로드 대상)
  - AppSetting report_template_* — 사내 보고서 템플릿 (해시 캐시가 가리키는 파일)
안전을 위해 기본은 조회만 하고, --apply를 줘야 삭제한다.

‼️ 반드시 운영 DB(VM의 Postgres)에 연결된 상태로 실행할 것.
Files API 스토리지는 API 키 단위라 로컬 sqlite와 운영 DB가 같은 스토리지를
본다. 로컬에서 --apply를 돌리면 운영 대화가 참조 중인 파일을 '고아'로 보고
지워버린다 (VM에서 docker compose exec로 실행하는 것이 정상 경로).
"""
from datetime import timedelta

import anthropic
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from ...models import AppSetting, ChatTurn

# 업로드 직후 아직 질문을 보내지 않은 파일을 지우지 않도록 두는 유예 기간
DEFAULT_GRACE_DAYS = 3


class Command(BaseCommand):
    help = '어디에서도 참조하지 않는 Files API 파일을 정리한다 (기본: 조회만)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=DEFAULT_GRACE_DAYS,
                            help=f'N일보다 오래된 파일만 대상 (기본 {DEFAULT_GRACE_DAYS})')
        parser.add_argument('--apply', action='store_true',
                            help='실제로 삭제한다 (기본은 대상만 출력)')

    def handle(self, *args, **options):
        if not settings.ANTHROPIC_API_KEY:
            raise CommandError('ANTHROPIC_API_KEY가 설정되지 않았습니다.')

        cutoff = timezone.now() - timedelta(days=options['days'])
        keep = _referenced_file_ids()
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

        orphans = [f for f in client.beta.files.list()
                   if f.id not in keep and f.created_at < cutoff]
        if not orphans:
            self.stdout.write('정리할 파일이 없습니다.')
            return

        for f in orphans:
            self.stdout.write(f'{f.id}  {f.filename}  {f.created_at:%Y-%m-%d}')
        if not options['apply']:
            self.stdout.write(self.style.WARNING(
                f'{len(orphans)}건이 삭제 대상입니다. 실제로 지우려면 --apply를 붙이세요.'))
            return

        deleted = 0
        for f in orphans:
            try:
                client.beta.files.delete(f.id)
                deleted += 1
            except anthropic.APIError as e:
                self.stderr.write(f'삭제 실패 {f.id}: {e}')
        self.stdout.write(self.style.SUCCESS(f'{deleted}건을 삭제했습니다.'))


def _referenced_file_ids():
    """지우면 안 되는 file_id 집합 (사용자 첨부·생성 문서·보고서 템플릿)."""
    keep = set()
    for attachments, files in ChatTurn.objects.values_list('attachments', 'files'):
        for entry in list(attachments or []) + list(files or []):
            if isinstance(entry, dict) and entry.get('file_id'):
                keep.add(entry['file_id'])

    # 템플릿 캐시 값은 "<파일해시>:<file_id>" 형식 (help_agent._template_file_id)
    for value in AppSetting.objects.filter(
            key__startswith='report_template_').values_list('value', flat=True):
        if ':' in (value or ''):
            keep.add(value.split(':', 1)[1])
    return keep
