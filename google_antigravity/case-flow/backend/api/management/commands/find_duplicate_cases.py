"""제목이 같은데 케이스가 갈린 묶음을 찾아 보고한다 (읽기 전용).

    python manage.py find_duplicate_cases
    python manage.py find_duplicate_cases --days 30

스레드를 회신마다 새로 만드는 메일러 탓에 한 대화가 여러 케이스로 쪼개지는
일이 있어, 동기화 후 점검용으로 쓴다. 제목이 반복되는 공지성 메일(Field
Notice, End of Sale 등)도 같은 묶음으로 잡히므로 병합 여부는 사람이 판단한다.
회신 접두어가 붙어 있어 지금 로직이라면 자동으로 이어붙었을 케이스에는
'병합 대상' 표시를 붙인다.
"""
from collections import defaultdict
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from ...models import Case, CaseEmail
from ...services.email_parser import clean_subject, has_reply_prefix
from ...services.gmail_sync import SUBJECT_MATCH_MIN_LENGTH


class Command(BaseCommand):
    help = '제목이 같은데 갈린 케이스 묶음을 보고한다 (변경 없음)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=0,
                            help='최근 N일 이내 메일만 검사 (기본: 전체)')

    def handle(self, *args, **options):
        emails = CaseEmail.objects.select_related('case').only(
            'subject', 'received_at', 'case_id', 'case__vendor')
        if options['days']:
            emails = emails.filter(
                received_at__gte=timezone.now() - timedelta(days=options['days']))

        groups = _group_by_subject(emails)
        if not groups:
            self.stdout.write('중복 의심 묶음이 없습니다.')
            return

        total = sum(len(ids) for ids, _ in groups)
        self.stdout.write(f'중복 의심 묶음 {len(groups)}건 / 관련 케이스 {total}건\n')
        for ids, subject in groups:
            self._report(ids, subject)

    def _report(self, ids, subject):
        cases = {case.pk: case for case in Case.objects.filter(pk__in=ids)}
        mails = defaultdict(list)
        for email in CaseEmail.objects.filter(case_id__in=ids).order_by('received_at'):
            mails[email.case_id].append(email)
        # 케이스 id 순이 아니라 대화 순 — 맨 앞이 대화를 시작한 케이스이고
        # 나머지가 거기에 이어붙었어야 할 후속이다
        ids = sorted(ids, key=lambda pk: mails[pk][0].received_at)

        total_mails = sum(len(mails[pk]) for pk in ids)
        self.stdout.write(f'■ {cases[ids[0]].vendor} | 케이스 {len(ids)}건 | '
                          f'메일 {total_mails}통 | 제목: {subject[:80]}')

        seen = set()   # 앞선 케이스들의 정리된 제목
        for pk in ids:
            case = cases[pk]
            first = mails[pk][0]
            # 지금 매칭 로직과 같은 조건 — 첫 메일이 회신이고 정리된 제목이
            # 앞선 케이스의 메일과 같으면 그 케이스에 이어붙었어야 한다.
            # (제목에 케이스 번호가 박힌 재접수는 제목이 달라 여기 걸리지 않는다)
            mergeable = (has_reply_prefix(first.subject)
                         and clean_subject(first.subject).lower() in seen)
            mark = ' ← 병합 대상' if mergeable else ''
            self.stdout.write(
                f'   - {case.case_id} [{case.status}] 메일 {len(mails[pk])}통 '
                f'번호={case.vendor_case_number or "-"} {case.summary[:45]}{mark}')
            seen.update(clean_subject(email.subject).lower() for email in mails[pk])
        self.stdout.write('')


def _group_by_subject(emails):
    """정리된 제목이 같은 케이스들을 묶는다.

    한 케이스의 제목 변형이 다른 케이스와 사슬처럼 이어질 수 있어
    (A=B, B=C) 유니온-파인드로 연결 성분을 만든다.
    Returns: [(케이스 id 목록, 대표 제목)] — 최근 대화 순
    """
    buckets = defaultdict(set)
    labels = {}
    for email in emails:
        cleaned = clean_subject(email.subject).lower()
        if len(cleaned) < SUBJECT_MATCH_MIN_LENGTH:
            continue
        key = (email.case.vendor, cleaned)
        buckets[key].add(email.case_id)
        labels.setdefault(key, clean_subject(email.subject))

    parent = {}

    def find(node):
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    group_labels = defaultdict(set)
    for key, ids in buckets.items():
        if len(ids) < 2:
            continue
        ids = sorted(ids)
        for other in ids[1:]:
            parent[find(other)] = find(ids[0])
        group_labels[find(ids[0])].add(labels[key])

    components = defaultdict(list)
    for case_id in list(parent):
        components[find(case_id)].append(case_id)

    groups = []
    for root, ids in components.items():
        ids = sorted(ids)
        last = CaseEmail.objects.filter(case_id__in=ids).order_by('-received_at').first()
        # 대표 제목은 가장 짧은 것 — 회신 껍데기가 가장 적게 남은 형태
        subject = min(group_labels[root], key=len)
        groups.append((last.received_at if last else None, ids, subject))

    groups.sort(key=lambda item: item[0], reverse=True)
    return [(ids, subject) for _, ids, subject in groups]
