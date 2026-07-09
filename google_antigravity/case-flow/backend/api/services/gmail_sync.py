"""Orchestrates a Gmail sync run: fetch -> parse -> AI analyze -> save."""
import logging

from django.utils import timezone

from ..models import Case, CaseEmail
from . import email_parser, gmail_client
from .analyzer import analyze_email, get_translation_model

logger = logging.getLogger(__name__)


def sync_gmail(max_results=50):
    """Fetch unprocessed vendor case mail and register it as Cases/CaseEmails.

    Returns a summary dict: counts of processed/created/updated/skipped.
    """
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

    ignore_reason = email_parser.find_ignore_reason(sender, subject)
    if ignore_reason:
        logger.info("Ignoring bulk mail %s (%s): %s", message_id, ignore_reason, subject)
        return 'ignored'

    vendor, direction = email_parser.detect_vendor_and_direction(
        sender, recipient,
        original_sender=(gmail_client.get_header(message, 'X-Original-Sender')
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
    case = _find_case(case_number, thread_id)
    is_new = case is None

    analysis = analyze_email(
        subject=subject,
        body=body,
        direction=direction,
        is_new_case=is_new,
        case_context=build_case_context(case) if case else '',
    )

    if is_new:
        case = _create_case(vendor, subject, body, analysis, case_number, thread_id)
    else:
        _backfill_identifiers(case, case_number, thread_id)

    _create_case_email(case, message_id, thread_id, direction, sender, recipient,
                       subject, body, received_at, analysis)
    apply_analysis_to_case(case, analysis, direction, received_at)
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
    CaseEmail.objects.create(
        case=case,
        gmail_message_id=message_id,
        gmail_thread_id=thread_id,
        direction=direction,
        sender=sender,
        recipient=recipient,
        subject=subject[:500],
        subject_ko=subject_ko[:500],
        body_original=body,
        body_ko=body_ko,
        received_at=received_at,
    )


def _find_case(case_number, thread_id):
    """Match by vendor case number first, then by Gmail thread."""
    if case_number:
        case = Case.objects.filter(vendor_case_number=case_number).first()
        if case:
            return case
    if thread_id:
        return Case.objects.filter(gmail_thread_id=thread_id).first()
    return None


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
