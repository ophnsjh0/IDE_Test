"""해결(Resolved)된 케이스에서 재사용 지식을 AI로 추출하는 백필 커맨드.

케이스당 AI 호출 1회 비용이 들며, 이미 지식이 추출된 케이스는 건너뛰므로
여러 번 실행해도 안전하다. 공지/행정 처리 케이스는 AI가 has_knowledge=false로
걸러 저장하지 않는다.

    python manage.py extract_knowledge --dry-run          # 저장 없이 추출 결과 미리보기
    python manage.py extract_knowledge --limit 5          # 앞 5건만
    python manage.py extract_knowledge --case C-1118      # 특정 케이스만 (상태 무관)
    python manage.py extract_knowledge --model claude-haiku-4-5
    python manage.py extract_knowledge --enrich           # 기존 지식 전체에 공식 문서
                                                          # 근거(references) 재탐색·저장
"""
from contextlib import nullcontext

from django.core.management.base import BaseCommand, CommandError

from api.models import Case, KnowledgeItem
from api.services import knowledge
from api.services.analyzer import generate_structured, translation_model_override


class Command(BaseCommand):
    help = 'Resolved 케이스에서 문제-원인-해결 지식을 추출해 KnowledgeItem(draft)으로 저장한다'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='DB에 저장하지 않고 추출 결과만 출력')
        parser.add_argument('--limit', type=int, default=0,
                            help='처리할 최대 케이스 수 (0=제한 없음)')
        parser.add_argument('--case', help='특정 케이스만 처리 (C-1118 또는 DB id, 상태 무관)')
        parser.add_argument('--model', help='이번 실행에서만 사용할 AI 모델 id')
        parser.add_argument('--enrich', action='store_true',
                            help='추출 대신 기존 지식 항목의 공식 문서 근거를 재탐색해 저장')

    def handle(self, *args, **options):
        if options['enrich']:
            return self._handle_enrich(options)
        cases = self._target_cases(options)
        if not cases:
            self.stdout.write('처리할 케이스가 없습니다.')
            return

        counts = {'created': 0, 'exists': 0, 'no_knowledge': 0, 'failed': 0}
        model = options.get('model')
        with translation_model_override(model) if model else nullcontext():
            for case in cases:
                self.stdout.write(f'{case.case_id} [{case.vendor}] {case.summary[:50]}')
                outcome, item, preview = self._extract(case, options['dry_run'])
                counts[outcome] += 1
                self._report(outcome, item, preview)

        mode = '(dry-run, 저장 안 함) ' if options['dry_run'] else ''
        self.stdout.write(self.style.SUCCESS(
            f"{mode}완료 — 생성 {counts['created']}건, 기존 {counts['exists']}건, "
            f"지식 없음 {counts['no_knowledge']}건, 실패 {counts['failed']}건"
        ))

    def _handle_enrich(self, options):
        items = KnowledgeItem.objects.select_related('case').order_by('id')
        if options['limit']:
            items = items[:options['limit']]
        items = list(items)
        if not items:
            self.stdout.write('지식 항목이 없습니다.')
            return

        counts = {'enriched': 0, 'none_relevant': 0, 'no_candidates': 0,
                  'unavailable': 0, 'failed': 0}
        model = options.get('model')
        with translation_model_override(model) if model else nullcontext():
            for item in items:
                outcome = knowledge.enrich_with_references(item)
                counts[outcome] += 1
                label = {
                    'enriched': self.style.SUCCESS(
                        f'근거 {len(item.references)}건 연결'),
                    'none_relevant': '후보는 있으나 관련 근거 없음',
                    'no_candidates': '검색 후보 없음',
                    'unavailable': self.style.ERROR('임베딩 사용 불가 (OPENAI_API_KEY 확인)'),
                    'failed': self.style.ERROR('실패 (서버 로그 확인)'),
                }[outcome]
                self.stdout.write(f'{item.knowledge_id} [{item.vendor}] '
                                  f'{item.title[:45]} — {label}')
                if outcome == 'unavailable':
                    break

        self.stdout.write(self.style.SUCCESS(
            f"완료 — 근거 연결 {counts['enriched']}건, 관련 없음 {counts['none_relevant']}건, "
            f"후보 없음 {counts['no_candidates']}건, 실패 {counts['failed']}건"
        ))

    def _target_cases(self, options):
        if options.get('case'):
            ref = options['case'].upper().removeprefix('C-')
            if not ref.isdigit():
                raise CommandError('--case는 C-1118 또는 DB id 형식이어야 합니다.')
            number = int(ref)
            if number > 1000:
                number -= 1000
            case = Case.objects.filter(id=number).first()
            if case is None:
                raise CommandError(f'케이스를 찾을 수 없습니다: {options["case"]}')
            return [case]

        qs = (Case.objects.filter(status='Resolved', knowledge_items__isnull=True)
              .prefetch_related('emails').order_by('id'))
        if options['limit']:
            qs = qs[:options['limit']]
        return list(qs)

    def _extract(self, case, dry_run):
        if not dry_run:
            outcome, item = knowledge.extract_knowledge(case)
            return outcome, item, None
        # dry-run: 저장 경로를 타지 않고 AI 결과만 확인
        if case.knowledge_items.exists():
            return 'exists', None, None
        result = generate_structured(knowledge.SYSTEM_PROMPT,
                                     knowledge.build_case_material(case),
                                     knowledge.KNOWLEDGE_SCHEMA)
        if result is None:
            return 'failed', None, None
        if not result.get('has_knowledge') or not (result.get('resolution') or '').strip():
            return 'no_knowledge', None, None
        return 'created', None, result

    def _report(self, outcome, item, preview):
        label = {
            'created': self.style.SUCCESS('  → 추출'),
            'exists': '  → 기존 항목 있음, 건너뜀',
            'no_knowledge': '  → 재사용 지식 없음',
            'failed': self.style.ERROR('  → 실패 (서버 로그 확인)'),
        }[outcome]
        self.stdout.write(label + (f' {item.knowledge_id}' if item else ''))
        if preview:
            self.stdout.write(f"    title: {preview['title']}")
            self.stdout.write('\n'.join('    ' + line
                                        for line in preview['resolution'].splitlines()))
