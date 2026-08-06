"""스레드 절단 등으로 중복 생성된 케이스들을 하나로 병합한다.

    python manage.py merge_cases C-1150 C-1153 C-1159 --dry-run
    python manage.py merge_cases C-1150 C-1153 C-1159

기본 대상(남길 케이스)은 첫 메일이 가장 이른 케이스이며 `--into`로 지정할 수 있다.
이메일·지식·연관 케이스를 대상 케이스로 옮기고, 조치 타임라인은 시각순으로
다시 합친 뒤 나머지 케이스를 삭제한다.
"""
import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from ...models import Case, CaseEmail, KnowledgeItem

# apply_analysis_to_case가 남기는 '[YYYY-MM-DD HH:MM 수신] …' 블록 머리
BLOCK_HEADER = re.compile(r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) [^\]]*\]', re.MULTILINE)

FILL_IF_EMPTY_FIELDS = ('vendor_case_number', 'description', 'resolution',
                        'device_model', 'device_serial', 'software_version')


class Command(BaseCommand):
    help = '중복 생성된 케이스들을 하나의 케이스로 병합한다'

    def add_arguments(self, parser):
        parser.add_argument('case_ids', nargs='+',
                            help='병합할 케이스 (C-1150 또는 150 형식)')
        parser.add_argument('--into', help='남길 케이스 (기본: 첫 메일이 가장 이른 케이스)')
        parser.add_argument('--dry-run', action='store_true',
                            help='변경 없이 병합 결과만 출력')

    def handle(self, *args, **options):
        cases = _load_cases(options['case_ids'])
        if len(cases) < 2:
            raise CommandError('병합하려면 케이스가 2건 이상 필요합니다.')

        vendors = {case.vendor for case in cases}
        if len(vendors) > 1:
            raise CommandError(f'벤더가 서로 다릅니다: {", ".join(sorted(vendors))}')

        # 기본 대상은 대화를 시작한 케이스 — 케이스 생성 순(id)은 동기화 순서라
        # 대화 순서와 다를 수 있으므로 첫 메일 시각으로 고른다
        target = (_load_cases([options['into']])[0] if options['into']
                  else min(cases, key=_first_activity_at))
        sources = [case for case in cases if case.pk != target.pk]
        if not sources:
            raise CommandError('--into 케이스만 지정되어 병합할 대상이 없습니다.')

        self.stdout.write(f'대상: {target.case_id} {target.summary}')
        for case in sources:
            self.stdout.write(f'  ← {case.case_id} '
                              f'(메일 {case.emails.count()}건) {case.summary}')

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('--dry-run: 변경하지 않았습니다.'))
            return

        with transaction.atomic():
            moved = merge_cases(target, sources)
        self.stdout.write(self.style.SUCCESS(
            f'{len(sources)}건을 {target.case_id}로 병합했습니다 (메일 {moved}건 이동).'))


def merge_cases(target, sources):
    """sources의 이메일·지식·타임라인을 target으로 옮기고 sources를 삭제한다.

    Returns: 옮긴 이메일 수
    """
    source_ids = [case.pk for case in sources]
    # 상태는 가장 최근 메일을 받은 케이스의 것을 따른다 (대화의 현재 상태).
    # 이메일을 옮기기 전에 정해야 한다 — 옮긴 뒤에는 원본 케이스가 빈 케이스가 된다.
    latest_status = max([target] + sources, key=_last_activity_at).status

    moved = CaseEmail.objects.filter(case_id__in=source_ids).update(case=target)
    KnowledgeItem.objects.filter(case_id__in=source_ids).update(case=target)

    for case in sources:
        for related in case.related_cases.exclude(pk__in=source_ids + [target.pk]):
            target.related_cases.add(related)

    for field in FILL_IF_EMPTY_FIELDS:
        if getattr(target, field):
            continue
        for case in sources:
            value = getattr(case, field)
            if value:
                setattr(target, field, value)
                break

    target.status = latest_status

    merge_note = (f"[{timezone.localtime().strftime('%Y-%m-%d %H:%M')} 시스템] "
                  f"스레드가 갈려 중복 생성된 케이스 "
                  f"{', '.join(case.case_id for case in sources)}를 "
                  f"이 케이스로 병합했습니다.")
    target.action_steps = _merge_action_steps(
        [target.action_steps] + [case.action_steps for case in sources], merge_note)

    # 메일이 늘었으니 지식 추출을 다시 받도록 검토 표시를 지운다
    target.knowledge_checked_at = None
    target.save()

    Case.objects.filter(pk__in=source_ids).delete()
    return moved


def _first_activity_at(case):
    first_email = case.emails.order_by('received_at').first()
    return first_email.received_at if first_email else case.created_at


def _last_activity_at(case):
    last_email = case.emails.order_by('-received_at').first()
    return last_email.received_at if last_email else case.updated_at


def _merge_action_steps(texts, note):
    """여러 케이스의 조치 타임라인을 시각순으로 합친다 (같은 블록은 1회만)."""
    blocks = []
    for text in texts:
        blocks.extend(_split_blocks(text))
    blocks.sort(key=lambda item: item[0])

    merged, seen = [], set()
    for _, block in blocks:
        if block in seen:
            continue
        seen.add(block)
        merged.append(block)
    merged.append(note)
    return '\n\n'.join(merged)


def _split_blocks(text):
    """타임라인 텍스트를 (정렬키, 블록) 목록으로 자른다.

    블록 본문에 빈 줄이 들어갈 수 있어 '\\n\\n' 분리로는 잘리지 않는다 —
    시각 머리('[2026-07-31 14:56 수신]')를 기준으로 자른다. 머리가 없는
    옛 텍스트는 정렬키를 빈 문자열로 둬 맨 앞에 오게 한다.
    """
    text = (text or '').strip()
    if not text:
        return []
    headers = list(BLOCK_HEADER.finditer(text))
    if not headers:
        return [('', text)]

    blocks = []
    head = text[:headers[0].start()].strip()
    if head:
        blocks.append(('', head))
    for index, match in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        blocks.append((match.group(1), text[match.start():end].strip()))
    return blocks


def _load_cases(raw_ids):
    """'C-1150' / '150' 형식의 인자를 케이스로 바꾼다 (오래된 순)."""
    pks = []
    for raw in raw_ids:
        value = (raw or '').strip().upper()
        # 표시 번호(C-1150)는 pk + 1000, 숫자만 주면 pk 그대로
        display = value.startswith('C-')
        if display:
            value = value[2:]
        if not value.isdigit():
            raise CommandError(f'케이스 번호 형식이 아닙니다: {raw}')
        pks.append(int(value) - 1000 if display else int(value))

    cases = list(Case.objects.filter(pk__in=set(pks)).order_by('pk'))
    found = {case.pk for case in cases}
    missing = [pk for pk in pks if pk not in found]
    if missing:
        raise CommandError('없는 케이스: ' + ', '.join(f'C-{1000 + pk}' for pk in missing))
    return cases
