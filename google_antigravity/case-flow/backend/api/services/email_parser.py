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

RE_PREFIX = re.compile(r'^\s*((re|fw|fwd|답장|전달)\s*:\s*)+', re.IGNORECASE)


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
    return None, 'inbound'


def extract_case_number(subject):
    for pattern in CASE_NUMBER_PATTERNS:
        match = pattern.search(subject or '')
        if match:
            return match.group(1)
    return None


def clean_subject(subject):
    return RE_PREFIX.sub('', subject or '').strip()


def parse_received_at(date_header):
    try:
        return parsedate_to_datetime(date_header)
    except (TypeError, ValueError):
        return None


# Sender local parts that are always bulk/marketing, never a TAC engineer.
MARKETING_SENDER_PREFIXES = ('marketing', 'newsletter', 'news', 'promo', 'events')


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
    query = '{' + ' '.join(parts) + '}'

    if settings.GMAIL_SYNC_LOOKBACK_DAYS > 0:
        query += f' newer_than:{settings.GMAIL_SYNC_LOOKBACK_DAYS}d'

    if settings.GMAIL_SYNC_EXCLUDE_SUBJECTS:
        exclusions = ' '.join(
            f'subject:"{kw}"' if ' ' in kw else f'subject:{kw}'
            for kw in settings.GMAIL_SYNC_EXCLUDE_SUBJECTS
        )
        query += ' -{' + exclusions + '}'

    return query


def find_ignore_reason(sender, subject):
    """Rule-based bulk-mail check run before AI analysis.

    Returns a short reason string when the mail should be discarded,
    or None when it looks like real case mail. Second net behind the
    Gmail query exclusions (subject: only matches whole words there).

    Deliberately does NOT use the List-Unsubscribe header: internal
    Google Groups add it to every relayed mail, including case mail.
    """
    subject_lower = (subject or '').lower()
    for keyword in settings.GMAIL_SYNC_EXCLUDE_SUBJECTS:
        if keyword.lower() in subject_lower:
            return f'subject keyword "{keyword}"'

    local_part = parseaddr(sender or '')[1].split('@', 1)[0].lower()
    for prefix in MARKETING_SENDER_PREFIXES:
        if local_part == prefix or local_part.startswith((prefix + '-', prefix + '.')):
            return f'marketing sender "{local_part}@"'

    return None
