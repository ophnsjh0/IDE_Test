"""Gmail API client: OAuth handling, message fetch, label management."""
import base64
import os

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
        return plain.strip()
    if html:
        try:
            from bs4 import BeautifulSoup
            return BeautifulSoup(html, 'html.parser').get_text(separator='\n').strip()
        except ImportError:
            return html.strip()
    return ''


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
