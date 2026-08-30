"""EVE-NG API 클라이언트.

EVE-NG 접근은 전부 이 모듈 안에서만 한다. 나중에 개인 Community 서버에서 Pro로
옮길 수 있어서, 바깥(뷰·모델·화면)이 EVE-NG의 응답 모양을 알지 못하게 막는다.
바깥으로 나가는 건 우리 용어로 번역한 dict뿐이다.
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

TIMEOUT = 15


class EvengError(RuntimeError):
    """EVE-NG와 통신하지 못했거나 EVE-NG가 실패를 돌려준 경우."""


class EvengNotConfigured(EvengError):
    """.env에 접속 정보가 없다 — 랩 없이도 앱은 동작해야 하므로 별도 예외."""


def is_configured():
    return bool(settings.EVENG_URL and settings.EVENG_USER and settings.EVENG_PASSWORD)


class EvengClient:
    """세션 하나를 들고 EVE-NG를 호출한다.

    EVE-NG 세션은 금방 만료돼 `User is not authenticated (90001)`을 돌려준다
    (실측 확인). 그래서 401/90001을 만나면 한 번 다시 로그인하고 재시도한다.
    """

    def __init__(self, base_url=None, user=None, password=None):
        self.base_url = (base_url or settings.EVENG_URL).rstrip('/')
        self.user = user or settings.EVENG_USER
        self.password = password or settings.EVENG_PASSWORD
        if not (self.base_url and self.user and self.password):
            raise EvengNotConfigured(
                'EVE-NG 접속 정보가 없습니다. .env의 CASEFLOW_EVENG_URL / _USER / _PASSWORD를 확인하세요.')
        self.session = requests.Session()
        self._logged_in = False

    # ------------------------------------------------------------ 저수준

    def login(self):
        url = f'{self.base_url}/api/auth/login'
        try:
            res = self.session.post(
                url, json={'username': self.user, 'password': self.password, 'html5': '-1'},
                timeout=TIMEOUT)
        except requests.RequestException as e:
            raise EvengError(f'EVE-NG에 연결할 수 없습니다: {e}') from e
        if res.status_code != 200 or (res.json() or {}).get('status') != 'success':
            raise EvengError('EVE-NG 로그인에 실패했습니다. 계정 정보를 확인하세요.')
        self._logged_in = True

    def _request(self, method, path, retry=True, **kwargs):
        if not self._logged_in:
            self.login()
        url = f'{self.base_url}{path}'
        try:
            res = self.session.request(method, url, timeout=TIMEOUT, **kwargs)
        except requests.RequestException as e:
            raise EvengError(f'EVE-NG 요청에 실패했습니다: {e}') from e

        try:
            body = res.json()
        except ValueError:
            raise EvengError(f'EVE-NG가 예상 밖의 응답을 보냈습니다 (HTTP {res.status_code}).')

        # 세션 만료 — 한 번만 다시 로그인해서 재시도한다
        if body.get('status') != 'success' and '90001' in str(body.get('message', '')):
            if retry:
                self.login()
                return self._request(method, path, retry=False, **kwargs)
            raise EvengError('EVE-NG 세션이 만료됐습니다.')

        if body.get('status') != 'success':
            raise EvengError(f"EVE-NG 오류: {body.get('message', '알 수 없음')}")
        return body.get('data')

    def get(self, path):
        return self._request('GET', path)

    # ------------------------------------------------------------ 조회

    def server_version(self):
        """/api/status의 버전. Pro로 옮겼을 때 분기할 근거로 기록해둔다."""
        return str((self.get('/api/status') or {}).get('version') or '')

    def list_labs(self, folder='/'):
        """서버에 있는 모든 랩의 경로 목록. 폴더가 있으면 재귀로 내려간다.

        지금 서버에는 폴더가 없지만 Pro는 사용자별 폴더를 쓸 수 있어서,
        랩 식별자를 파일명이 아니라 경로 전체로 다룬다.
        """
        data = self.get(f'/api/folders{folder}') or {}
        labs = [
            {'path': _join(folder, lab['file']), 'file': lab['file']}
            for lab in (data.get('labs') or [])
        ]
        for sub in (data.get('folders') or []):
            name = sub.get('name') or ''
            if name in ('.', '..') or not name:
                continue
            labs.extend(self.list_labs(_join(folder, name) + '/'))
        return labs

    def topology(self, lab_path):
        """랩 하나의 노드·네트워크·링크를 우리 모양으로 번역해 돌려준다.

        EVE-NG 원본 JSON은 그대로 내보내지 않는다 — 저장까지 흘러가면 Pro의
        스키마 차이가 화면과 에이전트 도구까지 번진다.
        """
        quoted = _quote(lab_path)
        raw_nodes = self.get(f'/api/labs/{quoted}/nodes') or {}
        raw_nets = self.get(f'/api/labs/{quoted}/networks') or {}
        raw_links = self.get(f'/api/labs/{quoted}/topology') or []

        nodes = {}
        for eve_id, n in raw_nodes.items():
            nodes[str(eve_id)] = {
                'eve_id': int(eve_id),
                # 이름이 우리 쪽 키다. eve_id·console 포트는 서버를 옮기면
                # 재부여되므로 갱신되는 값으로만 다룬다.
                'name': n.get('name') or f'node{eve_id}',
                'template': n.get('template') or '',
                'image': n.get('image') or '',
                'icon': n.get('icon') or '',
                'left': int(n.get('left') or 0),
                'top': int(n.get('top') or 0),
                'ram': int(n.get('ram') or 0),
                'cpu': int(n.get('cpu') or 0),
                'ethernet': int(n.get('ethernet') or 0),
                'console_url': n.get('url') or '',
                # 0=꺼짐, 그 외=프로세스 떠 있음. "부팅 완료"가 아니다 —
                # 준비 판정은 장비 관리 API 응답으로만 가능하다(Step 2).
                'running': int(n.get('status') or 0) != 0,
            }

        networks = {}
        for eve_id, net in raw_nets.items():
            networks[str(eve_id)] = {
                'eve_id': int(eve_id),
                'name': net.get('name') or f'network{eve_id}',
                'net_type': net.get('type') or '',
                'left': int(net.get('left') or 0),
                'top': int(net.get('top') or 0),
            }

        links = []
        for link in raw_links:
            source = _endpoint(link.get('source'), link.get('source_type'), nodes, networks)
            target = _endpoint(link.get('destination'), link.get('destination_type'),
                               nodes, networks)
            if source is None or target is None:
                continue
            links.append({
                'source': source[0], 'source_is_network': source[1],
                'source_port': link.get('source_label') or '',
                'target': target[0], 'target_is_network': target[1],
                'target_port': link.get('destination_label') or '',
            })

        return {
            'nodes': list(nodes.values()),
            'networks': list(networks.values()),
            'links': links,
        }

    # ------------------------------------------------------------ 전원

    def start_node(self, lab_path, eve_id):
        """노드 전원을 켠다. 돌아오는 건 '프로세스를 띄웠다'까지고, 부팅 완료가
        아니다 — 준비 판정은 lab_probe가 장비 관리 API를 찔러서 한다."""
        self.get(f'/api/labs/{_quote(lab_path)}/nodes/{int(eve_id)}/start')

    def stop_node(self, lab_path, eve_id):
        self.get(f'/api/labs/{_quote(lab_path)}/nodes/{int(eve_id)}/stop')

    def node_states(self, lab_path):
        """{노드 이름: 프로세스가 떠 있는가}. 상태만 필요할 때 토폴로지 전체를
        다시 받지 않으려고 따로 둔다(폴링이 매번 도는 자리)."""
        raw = self.get(f'/api/labs/{_quote(lab_path)}/nodes') or {}
        return {
            (n.get('name') or f'node{eve_id}'): int(n.get('status') or 0) != 0
            for eve_id, n in raw.items()
        }

    def icon(self, filename):
        """노드 아이콘 원본 바이트. 브라우저가 EVE-NG에 직접 붙지 않도록 중계한다."""
        if not self._logged_in:
            self.login()
        safe = filename.replace('/', '').replace('\\', '')  # 경로 탈출 차단
        try:
            res = self.session.get(f'{self.base_url}/images/icons/{safe}', timeout=TIMEOUT)
        except requests.RequestException as e:
            raise EvengError(f'아이콘을 가져오지 못했습니다: {e}') from e
        if res.status_code != 200:
            raise EvengError(f'아이콘을 찾을 수 없습니다: {safe}')
        return res.content, res.headers.get('Content-Type', 'application/octet-stream')


def _join(folder, name):
    return f"{folder.rstrip('/')}/{name}"


def _quote(lab_path):
    """랩 경로를 URL에 넣을 수 있게 인코딩. 슬래시는 그대로 둔다."""
    from urllib.parse import quote
    return quote(lab_path.lstrip('/'), safe='/')


def _endpoint(ref, kind, nodes, networks):
    """'node3' / 'network5' → (이름, 네트워크인가). 못 찾으면 None."""
    ref = str(ref or '')
    if kind == 'node' and ref.startswith('node'):
        entry = nodes.get(ref[4:])
        return (entry['name'], False) if entry else None
    if kind == 'network' and ref.startswith('network'):
        entry = networks.get(ref[7:])
        return (entry['name'], True) if entry else None
    return None
