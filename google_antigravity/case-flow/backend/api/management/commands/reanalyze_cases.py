"""이미 저장된 메일 원문으로 케이스 내용을 다시 분석/정리하는 커맨드.

Gmail을 다시 읽지 않고 DB의 CaseEmail.body_original을 사용한다.
케이스의 summary/description/action_steps/resolution/status를 재구성하므로
해당 필드의 수동 편집 내용은 덮어써진다.

기본적으로 Message Batches API를 사용해 토큰 비용을 50% 절감한다.
같은 케이스의 메일은 이전 분석이 다음 메일의 컨텍스트가 되므로 순서대로,
서로 다른 케이스는 독립이므로 "각 케이스의 N번째 메일"을 한 배치(라운드)로 묶는다.
즉시 결과가 필요하면 --no-batch로 기존 동기 방식을 사용한다.
"""
from django.core.management.base import BaseCommand

from api.models import Case
from api.services.analyzer import analyze_email, analyze_emails_batch
from api.services.gmail_sync import apply_analysis_to_case, build_case_context


class Command(BaseCommand):
    help = 'Re-run AI analysis on stored case emails and rebuild case fields'

    def add_arguments(self, parser):
        parser.add_argument('--case', type=str, default=None,
                            help="화면에 표시되는 케이스 ID(C-1027) 또는 DB id(27)")
        parser.add_argument('--no-batch', action='store_true',
                            help='Batch API(50%% 할인) 대신 메일별 동기 호출 사용')

    def handle(self, *args, **options):
        cases = Case.objects.filter(emails__isnull=False).distinct()
        if options['case']:
            case_id = self._resolve_case_id(options['case'])
            cases = cases.filter(id=case_id)

        if not cases.exists():
            self.stdout.write(self.style.WARNING('재분석할 케이스가 없습니다. (메일이 연결된 케이스만 대상)'))
            self.stdout.write('사용 가능한 케이스:')
            for c in Case.objects.filter(emails__isnull=False).distinct().order_by('id'):
                self.stdout.write(f'  {c.case_id} (--case {c.id}) 메일 {c.emails.count()}건 | {c.summary[:50]}')
            return

        if options['no_batch']:
            for case in cases:
                self._reanalyze(case)
        else:
            self._reanalyze_batch(list(cases))

    @staticmethod
    def _resolve_case_id(value):
        """'C-1027' / '1027' / '27' 어떤 형식이든 DB id로 변환."""
        value = value.strip().upper().removeprefix('C-')
        try:
            number = int(value)
        except ValueError:
            return -1
        # 화면 케이스 번호(C-1000 + id)로 입력한 경우
        if number > 1000 and not Case.objects.filter(id=number).exists():
            return number - 1000
        return number

    @staticmethod
    def _reset_case(case):
        """타임라인 필드는 처음부터 다시 쌓는다."""
        case.action_steps = ''
        case.resolution = ''
        case.status = 'Open'
        case.save()

    @staticmethod
    def _apply_email_analysis(case, email, analysis, is_first):
        email.subject_ko = (analysis.get('subject_ko') or '')[:500]
        email.body_ko = analysis.get('body_ko') or ''
        email.save()

        if is_first:
            case.summary = ((analysis.get('summary') or '').strip() or case.summary)[:200]
            case.description = (analysis.get('description') or '').strip() or case.description

        apply_analysis_to_case(case, analysis, email.direction, email.received_at)

    def _reanalyze_batch(self, cases):
        """케이스별 N번째 메일을 라운드 단위 배치로 분석 (토큰 비용 50% 할인)."""
        emails_by_case = {}
        failures = {}
        for case in cases:
            emails_by_case[case.id] = list(case.emails.order_by('received_at'))
            failures[case.id] = 0
            self._reset_case(case)

        total = sum(len(v) for v in emails_by_case.values())
        rounds = max((len(v) for v in emails_by_case.values()), default=0)
        self.stdout.write(
            f'케이스 {len(cases)}건 / 메일 {total}건 — Batch API로 {rounds}라운드 진행'
        )

        for round_index in range(rounds):
            requests, meta = {}, {}
            for case in cases:
                emails = emails_by_case[case.id]
                if round_index >= len(emails):
                    continue
                email = emails[round_index]
                is_first = round_index == 0
                custom_id = f'email-{email.id}'
                requests[custom_id] = dict(
                    subject=email.subject,
                    body=email.body_original,
                    direction=email.direction,
                    is_new_case=is_first,
                    case_context='' if is_first else build_case_context(case),
                )
                meta[custom_id] = (case, email, is_first)

            self.stdout.write(f'  라운드 {round_index + 1}/{rounds}: {len(requests)}건 제출, 완료 대기 중...')
            results = analyze_emails_batch(requests)

            for custom_id, analysis in results.items():
                case, email, is_first = meta[custom_id]
                if analysis is None:
                    failures[case.id] += 1
                    continue
                self._apply_email_analysis(case, email, analysis, is_first)

        for case in cases:
            failed = failures[case.id]
            style = self.style.SUCCESS if failed == 0 else self.style.WARNING
            self.stdout.write(style(
                f'{case.case_id} ({case.vendor}) 완료: status={case.status}, 분석 실패 {failed}건'
            ))

    def _reanalyze(self, case):
        """--no-batch: 메일별 동기 호출 (기존 방식)."""
        emails = list(case.emails.order_by('received_at'))
        self.stdout.write(f"{case.case_id} ({case.vendor}) — 메일 {len(emails)}건 재분석 중...")

        self._reset_case(case)

        failed = 0
        for index, email in enumerate(emails):
            is_first = (index == 0)
            analysis = analyze_email(
                subject=email.subject,
                body=email.body_original,
                direction=email.direction,
                is_new_case=is_first,
                case_context='' if is_first else build_case_context(case),
            )
            if analysis is None:
                failed += 1
                continue

            self._apply_email_analysis(case, email, analysis, is_first)

        style = self.style.SUCCESS if failed == 0 else self.style.WARNING
        self.stdout.write(style(
            f"  완료: status={case.status}, 분석 실패 {failed}건"
        ))
