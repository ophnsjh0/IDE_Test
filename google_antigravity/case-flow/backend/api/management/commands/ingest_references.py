"""reference_docs/<벤더>/*.pdf 를 임베딩해 벡터 검색 가능하게 만드는 커맨드.

파일 sha256 해시로 변경 감지 — 바뀐 파일만 처리하므로 반복 실행해도 안전하고,
새 문서를 폴더에 넣은 뒤 다시 실행하면 그 파일만 임베딩된다.

    python manage.py ingest_references            # 신규/변경 파일만
    python manage.py ingest_references --force    # 전체 재임베딩 (모델 교체 시)

임베딩 모델은 settings.EMBEDDING_MODEL (env CASEFLOW_EMBEDDING_MODEL,
기본 text-embedding-3-small). 전체 문서 재임베딩 비용은 $0.1 미만.
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from api.models import ReferenceDocument
from api.services import references


class Command(BaseCommand):
    help = '벤더 공식 문서 PDF를 청킹·임베딩한다 (변경된 파일만, --force로 전체)'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true',
                            help='해시가 같아도 전체 재처리 (임베딩 모델 교체 시)')

    def handle(self, *args, **options):
        files = references.scan_files()
        if not files:
            self.stdout.write(f'문서가 없습니다: {settings.REFERENCE_DOCS_DIR}/<벤더>/*.pdf')
            return
        self.stdout.write(f'임베딩 모델: {settings.EMBEDDING_MODEL}')

        counts = {'created': 0, 'updated': 0, 'skipped': 0, 'failed': 0}
        seen = set()
        for vendor, doc_type, relative_path, path in files:
            seen.add(relative_path)
            self.stdout.write(f'[{vendor}{"/" + doc_type if doc_type else ""}] {relative_path}')
            try:
                outcome = references.ingest_file(
                    vendor, doc_type, relative_path, path, force=options['force'],
                    log=lambda msg: self.stdout.write(msg))
            except references.EmbeddingUnavailable as e:
                raise SystemExit(f'중단: {e}')
            except Exception:
                counts['failed'] += 1
                self.stderr.write(self.style.ERROR('  실패 — 서버 로그 확인'))
                import logging
                logging.getLogger(__name__).exception('ingest failed: %s', relative_path)
                continue
            counts[outcome] += 1
            label = {'created': '신규 임베딩 완료', 'updated': '재임베딩 완료',
                     'skipped': '변경 없음, 건너뜀'}[outcome]
            self.stdout.write(f'  → {label}')

        # 폴더에서 삭제된 문서는 DB에서도 정리
        removed = ReferenceDocument.objects.exclude(filename__in=seen)
        removed_count = removed.count()
        if removed_count:
            for doc in removed:
                self.stdout.write(f'파일 삭제됨 → DB에서 제거: {doc.filename}')
            removed.delete()

        self.stdout.write(self.style.SUCCESS(
            f"완료 — 신규 {counts['created']}, 갱신 {counts['updated']}, "
            f"건너뜀 {counts['skipped']}, 실패 {counts['failed']}, 제거 {removed_count}"
        ))
