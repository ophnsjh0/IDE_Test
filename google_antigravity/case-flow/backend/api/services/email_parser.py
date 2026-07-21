"""Rules for mapping a vendor support email to a Case."""
import re
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

from django.conf import settings

# sender/recipient domain -> vendor name (Case.VENDOR_CHOICES)
VENDOR_DOMAINS = {
    'a10networks.com': 'A10',
    'arista.com': 'Arista',
    'hpe.com': 'HPE Aruba',
    'arubanetworks.com': 'HPE Aruba',
    'juniper.net': 'Juniper',
}

# Common TAC case-number patterns found in subject lines
CASE_NUMBER_PATTERNS = [
    re.compile(r'case\s*#?\s*[:\-]?\s*(\d{5,})', re.IGNORECASE),          # Case #2024051234
    re.compile(r'\bSR\s*#?\s*[:\-]?\s*([0-9][0-9\-]{4,})', re.IGNORECASE), # SR 5-123456789
    re.compile(r'\[(\d{5,})\]'),                                           # [00123456]
    re.compile(r'ticket\s*#?\s*[:\-]?\s*(\d{5,})', re.IGNORECASE),
]

# 'RE:(2) (2) 제목' — 일부 메일러(삼성 등)는 회신 횟수를 (n)으로 붙이므로 함께 벗긴다
RE_PREFIX = re.compile(r'^\s*((re|fw|fwd|답장|전달)\s*:\s*|\(\d+\)\s*)+', re.IGNORECASE)

# 케이스 오픈 템플릿의 'Serial Number : TH1015...' / 'Serial : HBG...' 줄
RE_SERIAL_NUMBER = re.compile(
    r'serial\s*(?:number|no\.?)?\s*[:\-]\s*([A-Z0-9][A-Z0-9\-]{5,})', re.IGNORECASE)

# 장비 정보 추출 패턴 (라벨 기반 우선, 토큰 폴백)
RE_LABEL_MODEL = re.compile(
    r'(?:hardware\s*platform|device\s*model|model)\s*(?:name|number)?\s*[:\-]\s*'
    r'([A-Za-z]{2,10}[\- ]?[0-9][\w\-]{0,25})', re.IGNORECASE)
RE_LABEL_VERSION = re.compile(
    r'(?:software|acos|eos|aos|firmware)[\s\-]*(?:version|ver\.?)?\s*[:\-]\s*'
    r'v?([0-9]+\.[0-9][\w\.\-]*)', re.IGNORECASE)
# 산문 속 'EOS 4.32.4M', 'ACOS 5.2.1-P10' 류 (벤더 OS명 + 버전)
RE_OS_VERSION = re.compile(r'\b(?:EOS|ACOS|AOS-CX|AOS)\s+v?([0-9]+\.[0-9][\w\.\-]*)')
# 제목의 [NHN-6.0.8], [samsung-4.32.4M] 류 버전 표기 (EOS의 4.32.4M처럼 접미 문자 허용)
RE_SUBJECT_VERSION = re.compile(
    r'\[[^\]]*?[\- ]([0-9]+\.[0-9]+(?:\.[0-9]+)?[A-Za-z]{0,2}(?:-[\w]+)?)\]')
# HPE RMA 부품 목록의 'EC-SFP-SR, S/N N4QAJHS' 줄
RE_SN_ITEM = re.compile(r'\bS/N\s*[:\-]?\s*([A-Z0-9]{6,20})\b', re.IGNORECASE)
# 라벨 없이 본문에 등장하는 장비명 토큰 (A10 TH계열, Arista DCS계열)
RE_MODEL_TOKEN = re.compile(r'\b(TH[0-9]{3,4}[A-Z]?(?:-[A-Z0-9]+)*|DCS-[0-9A-Za-z\-]+)\b')

DEVICE_SERIAL_MAX_ITEMS = 5


def domain_of(address):
    _, email = parseaddr(address or '')
    if '@' not in email:
        return ''
    return email.split('@', 1)[1].lower()


def vendor_from_domain(domain):
    for vendor_domain, vendor in VENDOR_DOMAINS.items():
        if domain == vendor_domain or domain.endswith('.' + vendor_domain):
            return vendor
    return None


def detect_vendor_and_direction(sender, recipient, original_sender='', cc=''):
    """Return (vendor, direction). Vendor mail address wins over ours.

    Mail relayed through an internal Google Group rewrites From to the
    group address ("'Name' via For ADC" <adc@...>); the real author is
    preserved in X-Original-Sender / Reply-To, so check those before
    falling back to the recipient lists.
    """
    for author in (sender, original_sender):
        author_vendor = vendor_from_domain(domain_of(author))
        if author_vendor:
            return author_vendor, 'inbound'
    # getaddresses handles commas inside quoted display names
    # ('"Xu, Chengri" <x@hpe.com>'); a naive comma split cuts the address
    # apart. It does not split on ';' (Outlook-style lists), so pre-split.
    recipient_fields = (recipient or '').split(';') + (cc or '').split(';')
    for _, addr in getaddresses(recipient_fields):
        recipient_vendor = vendor_from_domain(domain_of(addr))
        if recipient_vendor:
            return recipient_vendor, 'outbound'
    # 고객사↔당사 스레드([Caseopen] 등)는 벤더 도메인이 어디에도 없다.
    # 수신자/참조에 낀 사내 그룹 주소(adc@ → A10)로 벤더를 추정하고,
    # 방향은 작성자가 당사 도메인이면 outbound(당사→고객), 아니면 inbound.
    hints = settings.GROUP_VENDOR_HINTS
    if hints:
        our_domains = {address.split('@', 1)[1] for address in hints if '@' in address}
        for _, addr in getaddresses(recipient_fields + [sender or '', original_sender or '']):
            hinted_vendor = hints.get(addr.lower())
            if hinted_vendor:
                author_domain = domain_of(sender) or domain_of(original_sender)
                direction = 'outbound' if author_domain in our_domains else 'inbound'
                return hinted_vendor, direction
    return None, 'inbound'


def extract_case_number(subject):
    for pattern in CASE_NUMBER_PATTERNS:
        match = pattern.search(subject or '')
        if match:
            return match.group(1)
    return None


def clean_subject(subject):
    return RE_PREFIX.sub('', subject or '').strip()


def extract_serial_number(body):
    match = RE_SERIAL_NUMBER.search(body or '')
    if not match:
        return None
    serial = match.group(1).upper()
    # 'Serial : expired' 류 오탐 방지 — 실제 시리얼은 항상 숫자를 포함
    return serial if any(ch.isdigit() for ch in serial) else None


def extract_device_info(subject, body):
    """메일에서 장비 모델/시리얼/버전을 정규식으로 추출 (고신뢰 1차 패스).

    라벨이 명시된 패턴(A10 오픈 템플릿, S/N 라인아이템)을 우선하고,
    없으면 장비명 토큰/제목 버전 표기로 폴백한다. 못 찾은 필드는 빈 문자열 —
    산문에 박힌 정보는 2차 패스(AI 분석)가 채운다.
    """
    subject = subject or ''
    body = body or ''
    text = f'{subject}\n{body}'

    serial = extract_serial_number(body) or ''
    if not serial:
        # HPE RMA 부품 목록처럼 S/N이 여러 개인 경우 쉼표 병기
        items = list(dict.fromkeys(m.group(1).upper() for m in RE_SN_ITEM.finditer(body)))
        if items:
            serial = ', '.join(items[:DEVICE_SERIAL_MAX_ITEMS])
            if len(items) > DEVICE_SERIAL_MAX_ITEMS:
                serial += f' 외 {len(items) - DEVICE_SERIAL_MAX_ITEMS}개'

    model_match = RE_LABEL_MODEL.search(body) or RE_MODEL_TOKEN.search(text)
    model = model_match.group(1).strip('-') if model_match else ''

    version_match = (RE_LABEL_VERSION.search(body) or RE_SUBJECT_VERSION.search(subject)
                     or RE_OS_VERSION.search(body))
    version = version_match.group(1).rstrip('.-') if version_match else ''

    return {
        'device_model': model,
        'device_serial': serial,
        'software_version': version,
    }


def normalize_body(body, limit=10000):
    """본문 유사도 비교용 정규화: 인용줄(>) 제거, 공백 압축, 소문자화."""
    lines = [line for line in (body or '').splitlines()
             if not line.lstrip().startswith('>')]
    text = re.sub(r'\s+', ' ', ' '.join(lines)).strip().lower()
    return text[:limit]


def parse_received_at(date_header):
    try:
        return parsedate_to_datetime(date_header)
    except (TypeError, ValueError):
        return None


# Sender local parts that are always bulk/marketing, never a TAC engineer.
MARKETING_SENDER_PREFIXES = ('marketing', 'newsletter', 'news', 'promo', 'events')

# 자동 발송 전용 주소 — TAC 케이스 메일은 항상 회신 가능한 주소(엔지니어,
# support@)에서 오므로 no-reply 발신은 케이스가 아니다 (Arista Community
# Central, HPE 계정 안내 등).
NO_REPLY_SENDER_LOCALS = ('no-reply', 'noreply', 'do-not-reply', 'donotreply')

# 벤더 자동 공지 피드 제목. Arista는 'New End of Sale email notification',
# 'Security advisory Update email notification' 류 제목으로 공지를 보낸다.
# 케이스가 아니므로 버린다 — CaseFlow/Ignored 라벨이 남으므로 향후
# 공지/Advisory 피드 기능이 재수집할 수 있다.
NOTIFICATION_SUBJECT_KEYWORDS = ('email notification',)


def build_gmail_query():
    """Search query covering mail exchanged with any known vendor domain.

    Applies the sync filters from settings: a lookback window and
    subject-keyword exclusions, so bulk mail is dropped at the Gmail
    search stage (no fetch, no AI cost).
    """
    parts = []
    for domain in VENDOR_DOMAINS:
        parts.append(f'from:{domain}')
        parts.append(f'to:{domain}')
    # 벤더 도메인이 없는 고객사↔당사 케이스 스레드([Caseopen] 등)도 수집
    for keyword in settings.GMAIL_SYNC_INCLUDE_SUBJECTS:
        parts.append(f'subject:"{keyword}"' if ' ' in keyword else f'subject:{keyword}')
    query = '{' + ' '.join(parts) + '}'

    if settings.GMAIL_SYNC_LOOKBACK_DAYS > 0:
        query += f' newer_than:{settings.GMAIL_SYNC_LOOKBACK_DAYS}d'

    exclude_keywords = (list(settings.GMAIL_SYNC_EXCLUDE_SUBJECTS)
                        + list(NOTIFICATION_SUBJECT_KEYWORDS))
    if exclude_keywords:
        exclusions = ' '.join(
            f'subject:"{kw}"' if ' ' in kw else f'subject:{kw}'
            for kw in exclude_keywords
        )
        query += ' -{' + exclusions + '}'

    return query


def find_ignore_reason(sender, subject, original_sender=''):
    """Rule-based bulk-mail check run before AI analysis.

    Returns a short reason string when the mail should be discarded,
    or None when it looks like real case mail. Second net behind the
    Gmail query exclusions (subject: only matches whole words there).

    그룹 중계 메일은 From이 그룹 주소로 바뀌므로 발신자 규칙은
    original_sender(X-Original-Sender)에도 함께 적용한다.

    Deliberately does NOT use the List-Unsubscribe header: internal
    Google Groups add it to every relayed mail, including case mail.
    """
    subject_lower = (subject or '').lower()
    for keyword in settings.GMAIL_SYNC_EXCLUDE_SUBJECTS:
        if keyword.lower() in subject_lower:
            return f'subject keyword "{keyword}"'
    for keyword in NOTIFICATION_SUBJECT_KEYWORDS:
        if keyword in subject_lower:
            return f'vendor notification "{keyword}"'

    for address in (sender, original_sender):
        local_part = parseaddr(address or '')[1].split('@', 1)[0].lower()
        if not local_part:
            continue
        if local_part in NO_REPLY_SENDER_LOCALS:
            return f'no-reply sender "{local_part}@"'
        for prefix in MARKETING_SENDER_PREFIXES:
            if local_part == prefix or local_part.startswith((prefix + '-', prefix + '.')):
                return f'marketing sender "{local_part}@"'

    return None
