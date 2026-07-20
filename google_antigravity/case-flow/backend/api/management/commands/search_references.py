"""임베딩된 벤더 문서에서 질문으로 검색해보는 테스트 커맨드.

검증 질문 세트로 검색 품질을 눈으로 확인하는 용도 (질문당 임베딩 1회 — 비용 무시 수준).

    python manage.py search_references "VRRP preempt 동작 조건"
    python manage.py search_references "slb template 설정" --vendor A10 --top 3 --full
"""
from django.core.management.base import BaseCommand

from api.services import references


class Command(BaseCommand):
    help = '질문으로 레퍼런스 문서를 벡터 검색해 상위 청크를 출력한다'

    def add_arguments(self, parser):
        parser.add_argument('query', help='검색 질문 (한국어/영어 모두 가능)')
        parser.add_argument('--vendor', default='',
                            choices=['', 'A10', 'Arista', 'HPE Aruba', 'Juniper'])
        parser.add_argument('--type', default='', dest='doc_type',
                            help='문서 유형 폴더 필터 (config/release/issues 등)')
        parser.add_argument('--top', type=int, default=5, help='결과 수 (기본 5)')
        parser.add_argument('--full', action='store_true', help='청크 전문 출력')

    def handle(self, *args, **options):
        results = references.search(options['query'], vendor=options['vendor'],
                                    doc_type=options['doc_type'],
                                    top_k=options['top'])
        if not results:
            self.stdout.write('결과 없음 — ingest_references를 먼저 실행했는지 확인하세요.')
            return
        for rank, r in enumerate(results, start=1):
            type_label = f"/{r['doc_type']}" if r['doc_type'] else ''
            self.stdout.write(self.style.HTTP_INFO(
                f"#{rank} [{r['vendor']}{type_label}] {r['document']} {r['pages']} "
                f"(유사도 {r['score']})"))
            text = r['text'] if options['full'] else r['text'][:400] + '…'
            self.stdout.write(text + '\n')
