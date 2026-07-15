"""Gmail API client: OAuth handling, message fetch/send, label management."""
import base64
import os
import re
from email.mime.text import MIMEText

from django.conf import settings
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# gmail.modify: read mail + add/remove labels (needed to mark messages as processed)
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

PROCESSED_LABEL = 'CaseFlow/Processed'
# Bulk/marketing mail filtered out before case registration. Labeled (not
# deleted) so the decision stays auditable in Gmail.
IGNORED_LABEL = 'CaseFlow/Ignored'
# Mail fetched but not registered because no vendor domain was identified.
# Kept separate from Processed so these stay auditable and recoverable
# (remove the label and re-sync after improving the detection rules).
SKIPPED_LABEL = 'CaseFlow/Skipped'


class GmailAuthError(Exception):
    pass


def get_gmail_service():
    """Return an authenticated Gmail API service.

    First run opens a browser for OAuth consent (credentials.json required),
    afterwards the refresh token in token.json is reused silently.
    """
    creds = None
    token_path = settings.GMAIL_TOKEN_FILE
    credentials_path = settings.GMAIL_CREDENTIALS_FILE

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_path):
                raise GmailAuthError(
                    f"Gmail OAuth 클라이언트 파일이 없습니다: {credentials_path}. "
                    "Google Cloud Console에서 credentials.json을 내려받아 해당 경로에 두세요. "
                    "(GMAIL_SETUP.md 참고)"
                )
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, 'w') as f:
            f.write(creds.to_json())

    return build('gmail', 'v1', credentials=creds)


def send_email(to, subject, html_body):
    """HTML 메일 발송. 동기화에 쓰는 OAuth 토큰(gmail.modify가 발송 권한 포함)을 재사용한다.

    발신자는 토큰을 발급한 계정이 되고, 보낸 메일은 그 계정의 보낸편지함에 남는다.
    """
    service = get_gmail_service()
    message = MIMEText(html_body, 'html', 'utf-8')
    message['To'] = to
    message['Subject'] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return service.users().messages().send(userId='me', body={'raw': raw}).execute()


def get_or_create_label(service, name=PROCESSED_LABEL):
    """Return the label id for `name`, creating the label if needed."""
    labels = service.users().labels().list(userId='me').execute().get('labels', [])
    for label in labels:
        if label['name'] == name:
            return label['id']
    created = service.users().labels().create(
        userId='me',
        body={'name': name, 'labelListVisibility': 'labelShow', 'messageListVisibility': 'show'},
    ).execute()
    return created['id']


def list_unprocessed_messages(service, query, max_results=50):
    """List message ids matching `query` not yet processed or ignored."""
    full_query = (f'{query} -label:"{PROCESSED_LABEL}" -label:"{IGNORED_LABEL}" '
                  f'-label:"{SKIPPED_LABEL}"')
    response = service.users().messages().list(
        userId='me', q=full_query, maxResults=max_results
    ).execute()
    return response.get('messages', [])


def get_message(service, message_id):
    """Fetch a full message payload."""
    return service.users().messages().get(
        userId='me', id=message_id, format='full'
    ).execute()


def mark_processed(service, message_id, label_id):
    service.users().messages().modify(
        userId='me', id=message_id, body={'addLabelIds': [label_id]}
    ).execute()


def extract_body(payload):
    """Extract a plain-text body from a Gmail message payload.

    Prefers text/plain parts; falls back to stripped text/html.
    """
    plain, html = _walk_parts(payload)
    if plain:
        return _normalize_text(plain)
    if html:
        try:
            return html_to_text(html)
        except ImportError:
            return html.strip()
    return ''


# 줄바꿈으로 취급할 블록 요소. 인라인 태그(span/a/b 등)는 줄을 나누지 않아야
# 서명(명함)처럼 서식이 많은 부분이 "T / 070-… / M / …"으로 갈라지지 않는다.
_PARA_TAGS = ['p', 'blockquote', 'pre', 'table', 'ul', 'ol',
              'h1', 'h2', 'h3', 'h4', 'h5', 'h6']
_LINE_TAGS = ['div', 'tr', 'li', 'section', 'article', 'header', 'footer', 'hr']
# HTML 원본 텍스트에는 등장할 수 없는 제어문자를 개행 표식으로 쓴다
_LINE_MARK, _PARA_MARK = '\x00', '\x01'


def html_to_text(html):
    """HTML 메일 본문을 블록 구조를 살린 일반 텍스트로 변환한다.

    p 등 문단 요소는 빈 줄로, div/tr/li·<br>은 한 줄 개행으로 바꾸고
    HTML 소스의 들여쓰기·개행은 브라우저처럼 공백 1개로 접는다.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'head', 'title']):
        tag.decompose()
    for br in soup.find_all('br'):
        br.replace_with(_LINE_MARK)
    for cell in soup.find_all(['td', 'th']):  # 같은 행의 셀은 공백으로 구분
        cell.append(' ')
    for tag in soup.find_all(_PARA_TAGS):
        tag.insert_before(_PARA_MARK)
        tag.append(_PARA_MARK)
    for tag in soup.find_all(_LINE_TAGS):
        tag.insert_before(_LINE_MARK)
        tag.append(_LINE_MARK)
    text = soup.get_text()
    text = re.sub(r'\s+', ' ', text)  # 소스 개행/들여쓰기 → 공백 1개
    # 인접한 div/tr(닫힘+열림)이 만드는 연속 줄 표식은 개행 1개로 충분하다
    text = re.sub(f'(?:{_LINE_MARK} ?){{2,}}', _LINE_MARK, text)
    text = text.replace(_PARA_MARK, '\n\n').replace(_LINE_MARK, '\n')
    text = '\n'.join(line.strip() for line in text.split('\n'))
    return _normalize_text(text)


def _normalize_text(text):
    # 줄 끝 공백을 지우고 연속 빈 줄은 문단 구분 1개로 줄인다.
    # (앞쪽 들여쓰기는 CLI 출력·설정 덤프에 의미가 있을 수 있어 유지)
    text = re.sub(r'[ \t]+\n', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _walk_parts(payload):
    plain, html = '', ''
    mime = payload.get('mimeType', '')
    data = payload.get('body', {}).get('data')
    if data:
        decoded = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
        if mime == 'text/plain':
            plain += decoded
        elif mime == 'text/html':
            html += decoded
    for part in payload.get('parts', []) or []:
        p, h = _walk_parts(part)
        plain += p
        html += h
    return plain, html


def get_header(message, name):
    for header in message.get('payload', {}).get('headers', []):
        if header['name'].lower() == name.lower():
            return header['value']
    return ''
