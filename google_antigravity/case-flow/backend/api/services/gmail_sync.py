"""Orchestrates a Gmail sync run: fetch -> parse -> AI analyze -> save."""
import difflib
import fcntl
import logging
import os
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from ..models import Case, CaseEmail
from . import email_parser, gmail_client
from .analyzer import analyze_email, get_translation_model

logger = logging.getLogger(__name__)


class SyncInProgress(Exception):
    """다른 동기화가 이미 실행 중 — 웹 버튼 중복 클릭, 여러 PC 동시 실행, cron 겹침."""


class _DuplicateEmail(Exception):
    """저장 직전에 다른 동기화가 같은 메일을 먼저 넣은 경우 (경쟁 상태의 마지막 방어선)."""


_LOCK_FILE = os.path.join(settings.BASE_DIR, '.gmail_sync.lock')


def sync_gmail(max_results=50):
    """Fetch unprocessed vendor case mail and register it as Cases/CaseEmails.

    Returns a summary dict: counts of processed/created/updated/skipped.
    동시 실행은 파일 잠금으로 차단한다 (프로세스가 달라도 — 웹 + cron 겹침 대비).
    """
    lock_file = open(_LOCK_FILE, 'w')
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        raise SyncInProgress('Gmail 동기화가 이미 진행 중입니다. 잠시 후 다시 시도하세요.')
    try:
        return _sync_gmail(max_results)
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


def _sync_gmail(max_results):
    service = gmail_client.get_gmail_service()
    processed_label_id = gmail_client.get_or_create_label(service)
    ignored_label_id = gmail_client.get_or_create_label(service, gmail_client.IGNORED_LABEL)
    skipped_label_id = gmail_client.get_or_create_label(service, gmail_client.SKIPPED_LABEL)
    query = email_parser.build_gmail_query()
    message_refs = gmail_client.list_unprocessed_messages(service, query, max_results)

    # Gmail returns newest first; process oldest first so the case timeline
    # (action_steps, status transitions) builds up in chronological order.
    messages = [gmail_client.get_message(service, ref['id']) for ref in message_refs]
    messages.sort(key=lambda m: int(m.get('internalDate', 0)))

    summary = {'fetched': len(messages), 'cases_created': 0, 'emails_added': 0,
               'ignored': 0, 'no_vendor': 0, 'skipped': 0, 'errors': 0}

    for message in messages:
        try:
            result = _process_message(message)
            if result == 'ignored':
                summary['ignored'] += 1
                gmail_client.mark_processed(service, message['id'], ignored_label_id)
                continue
            if result == 'no_vendor':
                # Not silently dropped: gets its own label so the decision
                # is auditable and the mail is re-syncable after rule fixes.
                summary['no_vendor'] += 1
                gmail_client.mark_processed(service, message['id'], skipped_label_id)
                continue
            if result == 'created':
                summary['cases_created'] += 1
                summary['emails_added'] += 1
            elif result == 'added':
                summary['emails_added'] += 1
            else:
                summary['skipped'] += 1
            gmail_client.mark_processed(service, message['id'], processed_label_id)
        except Exception:
            logger.exception("Failed to process Gmail message %s", message.get('id'))
            summary['errors'] += 1

    return summary


def _process_message(message):
    """Process one Gmail message.

    Returns 'created' (new case), 'added' (email added to existing case),
    'ignored' (bulk mail, discard with the Ignored label), 'no_vendor'
    (no vendor domain identified, park with the Skipped label), or
    'skipped' (already in the DB).
    """
    message_id = message['id']
    if CaseEmail.objects.filter(gmail_message_id=message_id).exists():
        return 'skipped'

    sender = gmail_client.get_header(message, 'From')
    recipient = gmail_client.get_header(message, 'To')
    subject = gmail_client.get_header(message, 'Subject')
    date_header = gmail_client.get_header(message, 'Date')
    thread_id = message.get('threadId', '')

    x_original_sender = gmail_client.get_header(message, 'X-Original-Sender')

    ignore_reason = email_parser.find_ignore_reason(
        sender, subject, original_sender=x_original_sender)
    if ignore_reason:
        logger.info("Ignoring bulk mail %s (%s): %s", message_id, ignore_reason, subject)
        return 'ignored'

    vendor, direction = email_parser.detect_vendor_and_direction(
        sender, recipient,
        original_sender=(x_original_sender
                         or gmail_client.get_header(message, 'Reply-To')),
        cc=gmail_client.get_header(message, 'Cc'),
    )
    if vendor is None:
        logger.warning("No vendor identified for %s (from=%s): %s",
                       message_id, sender, subject)
        return 'no_vendor'

    body = gmail_client.extract_body(message.get('payload', {}))
    received_at = _ensure_aware(email_parser.parse_received_at(date_header)) or timezone.now()

    case_number = email_parser.extract_case_number(subject)
    case = _find_case(case_number, thread_id, vendor, subject, body)
    is_new = case is None

    analysis = analyze_email(
        subject=subject,
        body=body,
        direction=direction,
        is_new_case=is_new,
        case_context=build_case_context(case) if case else '',
    )

    # 메일 1건의 DB 반영을 원자적으로 묶는다 — 중간 실패 시 이메일 없는
    # 빈 케이스 같은 반쪽 상태가 남지 않도록.
    try:
        with transaction.atomic():
            if is_new:
                case = _create_case(vendor, subject, body, analysis, case_number, thread_id)
            else:
                _backfill_identifiers(case, case_number, thread_id)

            _create_case_email(case, message_id, thread_id, direction, sender, recipient,
                               subject, body, received_at, analysis)
            apply_device_info(case, subject, body, analysis)
            apply_analysis_to_case(case, analysis, direction, received_at)
    except _DuplicateEmail:
        logger.info("Message %s was saved by a concurrent sync; skipping", message_id)
        return 'skipped'
    return 'created' if is_new else 'added'


def build_case_context(case):
    """기존 케이스 이력을 분석 프롬프트에 넣을 텍스트로 요약."""
    lines = [
        f"요약: {case.summary}",
        f"현재 상태: {case.status}",
    ]
    if case.action_steps:
        # 최근 조치 이력만 (너무 길어지지 않게 뒤에서 1500자)
        lines.append(f"최근 조치 이력:\n{case.action_steps[-1500:]}")
    return '\n'.join(lines)


DEVICE_INFO_FIELDS = ('device_model', 'device_serial', 'software_version')


def apply_device_info(case, subject, body, analysis):
    """장비 정보 추출·반영: 정규식 1차 -> AI 분석값 2차, 빈 필드만 채운다.

    저장은 이후 apply_analysis_to_case의 save()가 담당한다.
    """
    extracted = email_parser.extract_device_info(subject, body)
    for field in DEVICE_INFO_FIELDS:
        if getattr(case, field):
            continue
        value = extracted.get(field) or ((analysis or {}).get(field) or '').strip()
        if value:
            max_length = case._meta.get_field(field).max_length
            setattr(case, field, value[:max_length])


def apply_analysis_to_case(case, analysis, direction, received_at):
    """분석 결과를 케이스 필드에 반영.

    - action_steps: 수신/발신 시각 기준으로 누적 append (타임라인)
    - resolution: 해결 내용이 감지됐고 비어 있을 때만 채움
    - status: AI 판단값으로 자동 전환 (상세 페이지에서 수동 변경 가능)
    """
    if analysis is None:
        case.save()  # updated_at 갱신
        return

    case.analyzed_by = get_translation_model()

    stamp = timezone.localtime(received_at).strftime('%Y-%m-%d %H:%M')
    direction_label = '수신' if direction == 'inbound' else '발신'

    action = (analysis.get('action_update') or '').strip()
    if action:
        block = f"[{stamp} {direction_label}] {action}"
        case.action_steps = f"{case.action_steps}\n\n{block}" if case.action_steps else block

    resolution = (analysis.get('resolution') or '').strip()
    if resolution and not case.resolution:
        case.resolution = resolution

    status = analysis.get('suggested_status')
    if status in ('Open', 'Pending', 'Resolved'):
        case.status = status

    case.save()


def _create_case_email(case, message_id, thread_id, direction, sender, recipient,
                       subject, body, received_at, analysis):
    subject_ko = (analysis or {}).get('subject_ko', '') or ''
    body_ko = (analysis or {}).get('body_ko', '') or ''
    _, created = CaseEmail.objects.get_or_create(
        gmail_message_id=message_id,
        defaults=dict(
            case=case,
            gmail_thread_id=thread_id,
            direction=direction,
            sender=sender,
            recipient=recipient,
            subject=subject[:500],
            subject_ko=subject_ko[:500],
            body_original=body,
            body_ko=body_ko,
            received_at=received_at,
        ),
    )
    if not created:
        # _process_message 초입의 중복 체크 이후에 다른 동기화가 먼저 저장한 것.
        # 예외로 트랜잭션 전체를 되돌려 이 실행이 만든 케이스/변경도 함께 취소한다.
        raise _DuplicateEmail(message_id)


def _find_case(case_number, thread_id, vendor=None, subject='', body=''):
    """5단계 케이스 매칭: 벤더 케이스 번호 -> Gmail 스레드 ->
    케이스 오픈 키워드 제목 완전 일치 -> 확인 메일에 포함된 원본 제목 ->
    본문 유사도."""
    if case_number:
        case = Case.objects.filter(vendor_case_number=case_number).first()
        if case:
            return case
    if thread_id:
        case = Case.objects.filter(gmail_thread_id=thread_id).first()
        if case:
            return case
        # 케이스 병합 등으로 대표 스레드가 아니게 된 스레드는 이메일로 역추적
        email = (CaseEmail.objects.filter(gmail_thread_id=thread_id)
                 .select_related('case').first())
        if email:
            return email.case
    if vendor:
        case = _find_case_by_exact_subject(vendor, subject)
        if case:
            return case
        case = _find_case_by_embedded_subject(vendor, subject, case_number)
        if case:
            return case
        return _find_case_by_body_similarity(vendor, body)
    return None


# 제목 폴백 매칭 파라미터: 짧은 제목 오탐 방지 최소 길이, 후보 케이스 탐색 기간
SUBJECT_MATCH_MIN_LENGTH = 10
SUBJECT_MATCH_WINDOW_DAYS = 60


def _find_case_by_exact_subject(vendor, subject):
    """고객사↔당사 케이스 스레드([Caseopen] 등)가 스레드 절단으로 갈릴 때의 폴백.

    삼성 등 일부 메일러는 회신할 때마다 새 Gmail 스레드를 만들고 제목에
    'RE:(2) (2)' 카운터를 붙여 번호/스레드 매칭이 모두 실패한다. 케이스 오픈
    키워드(GMAIL_SYNC_INCLUDE_SUBJECTS)가 제목에 있는 메일에 한해, 정리된
    제목이 정확히 같은 케이스와 병합한다 — 오픈 키워드 제목은 케이스당 한 번
    작성되므로 제목이 반복되는 공지성 메일과 달리 동일 제목 = 동일 케이스다.
    """
    subject_lower = (subject or '').lower()
    if not any(keyword.lower() in subject_lower
               for keyword in settings.GMAIL_SYNC_INCLUDE_SUBJECTS):
        return None
    cleaned = email_parser.clean_subject(subject).lower()
    if len(cleaned) < SUBJECT_MATCH_MIN_LENGTH:
        return None

    candidates = Case.objects.filter(
        vendor=vendor,
        created_at__gte=timezone.now() - timedelta(days=SUBJECT_MATCH_WINDOW_DAYS),
    )
    for case in candidates.order_by('-created_at'):
        first_email = case.emails.order_by('received_at').first()
        if first_email is None:
            continue
        if email_parser.clean_subject(first_email.subject).lower() == cleaned:
            return case
    return None


def _find_case_by_embedded_subject(vendor, subject, case_number):
    """케이스 오픈 메일과 벤더 접수 확인 메일이 서로 다른 스레드로 갈릴 때의 폴백.

    Arista는 엔지니어가 보낸 오픈 메일(SR 번호 없음)과 별개 스레드로
    'New ... Case: SR 834065 <원본 제목>' 확인 메일을 보내므로 번호/스레드
    매칭이 모두 실패해 케이스가 중복 생성된다. 확인 메일 제목에 원본 제목이
    그대로 포함되는 점을 이용해, 두 제목 중 짧은 쪽이 긴 쪽에 포함되면 같은
    케이스로 본다. 케이스 번호 유무가 서로 반대인 후보만 보므로(번호 달린
    메일 ↔ 번호 없는 케이스, 또는 그 반대) 제목이 똑같이 반복되는 공지성
    메일끼리 오병합되지는 않는다.
    """
    cleaned = email_parser.clean_subject(subject).lower()
    if len(cleaned) < SUBJECT_MATCH_MIN_LENGTH:
        return None

    no_number = Q(vendor_case_number__isnull=True) | Q(vendor_case_number='')
    candidates = Case.objects.filter(
        vendor=vendor,
        created_at__gte=timezone.now() - timedelta(days=SUBJECT_MATCH_WINDOW_DAYS),
    )
    candidates = candidates.filter(no_number) if case_number else candidates.exclude(no_number)

    for case in candidates.order_by('-created_at'):
        first_email = case.emails.order_by('received_at').first()
        if first_email is None:
            continue
        original = email_parser.clean_subject(first_email.subject).lower()
        shorter, longer = sorted((cleaned, original), key=len)
        if len(shorter) >= SUBJECT_MATCH_MIN_LENGTH and shorter in longer:
            return case
    return None


# 본문 유사도 폴백 파라미터: 후보 탐색 기간/개수, 오탐 방지 최소 본문 길이,
# 병합 임계값(시리얼 번호가 일치하면 재발송 시 로그 몇 줄 추가된 경우까지 완화)
BODY_MATCH_WINDOW_DAYS = 14
BODY_MATCH_MAX_CANDIDATES = 200
BODY_MATCH_MIN_LENGTH = 200
BODY_MATCH_THRESHOLD = 0.95
BODY_MATCH_SERIAL_THRESHOLD = 0.90


def _find_case_by_body_similarity(vendor, body):
    """제목을 바꿔 재발송해 스레드가 갈린 동일 접수 메일의 폴백 매칭.

    엔지니어가 같은 케이스 오픈 메일을 제목만 수정해 다시 보내면 Gmail
    스레드가 분리되어 번호/스레드/제목 매칭이 모두 실패한다. 같은 벤더의
    최근 이메일과 정규화 본문 유사도를 비교해 사실상 같은 본문이면 기존
    케이스로 병합하고, 케이스에 병합 표시를 남긴다.
    """
    normalized = email_parser.normalize_body(body)
    if len(normalized) < BODY_MATCH_MIN_LENGTH:
        return None

    serial = email_parser.extract_serial_number(body)
    since = timezone.now() - timedelta(days=BODY_MATCH_WINDOW_DAYS)
    emails = (CaseEmail.objects
              .filter(case__vendor=vendor, created_at__gte=since)
              .select_related('case')
              .order_by('-received_at')[:BODY_MATCH_MAX_CANDIDATES])

    for email in emails:
        candidate = email_parser.normalize_body(email.body_original)
        if len(candidate) < BODY_MATCH_MIN_LENGTH:
            continue
        threshold = BODY_MATCH_THRESHOLD
        if serial and serial == email_parser.extract_serial_number(email.body_original):
            threshold = BODY_MATCH_SERIAL_THRESHOLD
        matcher = difflib.SequenceMatcher(None, normalized, candidate)
        # ratio()는 비싸므로 상한 근사치로 먼저 거른다
        if matcher.real_quick_ratio() < threshold or matcher.quick_ratio() < threshold:
            continue
        ratio = matcher.ratio()
        if ratio >= threshold:
            logger.info("Body-similarity merge (%.3f) into case %s", ratio, email.case_id)
            _mark_duplicate_merge(email.case, ratio)
            return email.case
    return None


def _mark_duplicate_merge(case, ratio):
    """유사도 병합을 케이스 타임라인에 남겨 오병합 시 추적 가능하게 한다."""
    stamp = timezone.localtime(timezone.now()).strftime('%Y-%m-%d %H:%M')
    block = (f"[{stamp} 시스템] 제목이 다른 중복 접수 메일을 "
             f"본문 유사도({ratio:.0%})로 이 케이스에 병합했습니다.")
    case.action_steps = f"{case.action_steps}\n\n{block}" if case.action_steps else block


def _backfill_identifiers(case, case_number, thread_id):
    """스레드 후속 메일에서 알게 된 식별자를 케이스에 보충."""
    changed = False
    if case_number and not case.vendor_case_number:
        case.vendor_case_number = case_number
        changed = True
    if thread_id and not case.gmail_thread_id:
        case.gmail_thread_id = thread_id
        changed = True
    if changed:
        case.save()


def _create_case(vendor, subject, body, analysis, case_number, thread_id):
    if analysis:
        summary = (analysis.get('summary') or '').strip()
        description = (analysis.get('description') or '').strip()
    else:
        summary, description = '', ''

    # 분석 실패 시 폴백: 제목/본문 원문 사용
    summary = summary or email_parser.clean_subject(subject) or '(제목 없음)'
    description = description or body

    return Case.objects.create(
        vendor=vendor,
        status='Open',
        summary=summary[:200],
        description=description[:10000],
        source='email',
        vendor_case_number=case_number,
        gmail_thread_id=thread_id or None,
    )


def _ensure_aware(dt):
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return timezone.make_aware(dt)
    return dt
