"""장비 드라이버 — 랩 장비에 붙어 사실을 읽는다.

Step 3에서는 **읽기만** 한다. 설정을 바꾸는 경로는 Step 4에서 별도로 붙인다.

벤더별 함정은 전부 실측으로 확인된 것들이다:
- A10은 SSH CLI 스크래핑이 불안정하다(enable이 산발적으로만 잡히고 "System is
  ready now" 배너가 명령마다 끼어든다). aXAPI가 사실상 유일한 경로다.
- A10 aXAPI는 DELETE에도 Content-Type: application/json이 없으면 415를 돌려준다.
  (Step 4에서 쓸 때 걸린다 — 세션 헤더에 미리 박아둔다.)
- Arista eAPI는 Management1이 vrf mgmt에 있으면 mgmt VRF에 바인딩해야 관리
  IP로 붙는다. 안 돼 있으면 포트 자체가 열리지 않는다.
"""
import logging
import re

import requests
import urllib3

logger = logging.getLogger(__name__)

TIMEOUT = 10

# 장비 출력은 크다(show tech는 수 MB). 모델에게 넘어가는 경로가 있으므로
# 여기서 끊는다 — 판정은 코드가 하니 원문 전체가 필요하지 않다.
MAX_OUTPUT_CHARS = 20000

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class DriverError(RuntimeError):
    """장비와 통신하지 못했거나 장비가 오류를 돌려준 경우."""


def truncate(text):
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return (text[:MAX_OUTPUT_CHARS]
            + f'\n\n[이하 생략 — 전체 {len(text):,}자 중 앞 {MAX_OUTPUT_CHARS:,}자]')


def normalize_port(name):
    """포트 이름을 비교 가능한 모양으로 줄인다.

    같은 포트를 EVE-NG는 'E1', 장비는 'Ethernet1', LLDP는 'Et1'이라 부른다.
    첫 글자와 숫자만 남겨 ('e', '1') 꼴로 맞춘다 — 완벽하지 않지만 랩에서
    쓰는 이름들(E/Eth/Ethernet/Mgmt)에는 통한다. 못 맞추면 원문을 그대로 쓴다.
    """
    text = (name or '').strip().lower()
    match = re.match(r'^([a-z]+)[\s/]*([\d/.]+)$', text)
    if not match:
        return text
    return f'{match.group(1)[0]}{match.group(2)}'


class DeviceDriver:
    """읽기 전용 인터페이스. Step 4에서 apply/delete가 여기에 붙는다."""

    vendor = ''

    def __init__(self, access):
        self.access = access
        self.host = access.mgmt_ip

    def hostname(self):
        """장비가 스스로 부르는 이름. 등록된 노드 이름과 대조해
        '엉뚱한 장비에 붙었는지'를 잡는다."""
        raise NotImplementedError

    def lldp_neighbors(self):
        """[{'local_port', 'remote_host', 'remote_port'}]. 지원 안 하면 None."""
        return None

    # ---- Step 4에서 쓰는 쓰기·조회 ----

    def run_command(self, command):
        """읽기 명령 하나를 돌려 출력 문자열을 받는다."""
        raise NotImplementedError

    def apply(self, commands):
        """설정 명령을 넣는다. 되돌리기는 호출자(원장)가 책임진다."""
        raise NotImplementedError


class AristaDriver(DeviceDriver):
    """eAPI (JSON-RPC). EOS는 구조화 출력이 안정적이라 파싱이 단순하다."""

    vendor = 'arista'

    def _run(self, commands):
        try:
            res = requests.post(
                f'http://{self.host}/command-api',
                json={'jsonrpc': '2.0', 'method': 'runCmds',
                      'params': {'version': 1, 'cmds': commands, 'format': 'json'},
                      'id': 'caseflow'},
                auth=(self.access.username, self.access.password), timeout=TIMEOUT)
        except requests.RequestException as e:
            raise DriverError(f'eAPI 연결 실패 ({self.host}): {e}') from e
        body = res.json()
        if 'error' in body:
            # eAPI는 200으로 오류를 돌려준다 — 상태 코드만 보면 놓친다
            raise DriverError(f"eAPI 오류: {body['error'].get('message', body['error'])}")
        return body['result']

    def hostname(self):
        return self._run(['show hostname'])[0].get('hostname', '')

    def run_command(self, command):
        """text 포맷으로 받는다 — 검증은 문자열 포함 여부로 하기 때문."""
        res = self._run_text([command])
        return truncate(res[0].get('output', ''))

    def _run_text(self, commands):
        try:
            res = requests.post(
                f'http://{self.host}/command-api',
                json={'jsonrpc': '2.0', 'method': 'runCmds',
                      'params': {'version': 1, 'cmds': commands, 'format': 'text'},
                      'id': 'caseflow'},
                auth=(self.access.username, self.access.password), timeout=TIMEOUT)
        except requests.RequestException as e:
            raise DriverError(f'eAPI 연결 실패 ({self.host}): {e}') from e
        body = res.json()
        if 'error' in body:
            raise DriverError(f"eAPI 오류: {body['error'].get('message', body['error'])}")
        return body['result']

    def apply(self, commands):
        """configure 모드로 넣는다. EOS는 명령 하나라도 틀리면 전체가 실패한다."""
        self._run_text(['enable', 'configure'] + list(commands))

    def lldp_neighbors(self):
        rows = self._run(['show lldp neighbors'])[0].get('lldpNeighbors', [])
        return [{
            'local_port': row.get('port', ''),
            'remote_host': row.get('neighborDevice', ''),
            'remote_port': row.get('neighborPort', ''),
        } for row in rows]


class A10Driver(DeviceDriver):
    """aXAPI v3. 인증하면 signature를 주고, 이후 Authorization 헤더에 싣는다."""

    vendor = 'a10'

    def __init__(self, access):
        super().__init__(access)
        self.session = requests.Session()
        # DELETE에도 이 헤더가 없으면 415다(실측). 세션에 박아두고 잊는다.
        self.session.headers['Content-Type'] = 'application/json'
        self._authed = False

    def _auth(self):
        try:
            res = self.session.post(
                f'https://{self.host}/axapi/v3/auth',
                json={'credentials': {'username': self.access.username,
                                      'password': self.access.password}},
                timeout=TIMEOUT, verify=False)
        except requests.RequestException as e:
            raise DriverError(f'aXAPI 연결 실패 ({self.host}): {e}') from e
        signature = ((res.json() or {}).get('authresponse') or {}).get('signature')
        if res.status_code != 200 or not signature:
            raise DriverError('aXAPI 인증에 실패했습니다. 계정 정보를 확인하세요.')
        self.session.headers['Authorization'] = f'A10 {signature}'
        self._authed = True

    def _get(self, path):
        if not self._authed:
            self._auth()
        try:
            res = self.session.get(f'https://{self.host}/axapi/v3{path}',
                                   timeout=TIMEOUT, verify=False)
        except requests.RequestException as e:
            raise DriverError(f'aXAPI 요청 실패: {e}') from e
        if res.status_code != 200:
            raise DriverError(f'aXAPI 오류 (HTTP {res.status_code}): {res.text[:200]}')
        return res.json()

    def cli(self, commands):
        """CLI를 aXAPI로 태운다. SSH 스크래핑을 쓰지 않는 이유는 모듈 설명 참고."""
        if not self._authed:
            self._auth()
        try:
            res = self.session.post(f'https://{self.host}/axapi/v3/clideploy',
                                    json={'commandList': list(commands)},
                                    timeout=TIMEOUT, verify=False)
        except requests.RequestException as e:
            raise DriverError(f'aXAPI clideploy 실패: {e}') from e
        if res.status_code != 200:
            raise DriverError(f'clideploy 오류 (HTTP {res.status_code}): {res.text[:200]}')
        return truncate(res.text)

    def hostname(self):
        return ((self._get('/hostname') or {}).get('hostname') or {}).get('value', '')

    def run_command(self, command):
        return self.cli([command])

    def apply(self, commands):
        """configure 모드 명령을 clideploy로 태운다."""
        self.cli(['configure'] + list(commands))

    def lldp_neighbors(self):
        """A10은 LLDP 이웃을 CLI 출력으로만 준다 — 표 형태를 줄 단위로 읽는다.

        형식이 버전마다 다를 수 있어, 못 읽으면 빈 목록이 아니라 None을 돌려
        '지원 안 함'으로 처리한다(없는 것과 못 읽는 것은 다르다).
        """
        try:
            text = self.cli(['show lldp neighbor'])
        except DriverError:
            return None
        rows = []
        for line in text.splitlines():
            # <local port> <remote host> <remote port> ... 형태만 취한다
            parts = line.split()
            if len(parts) >= 3 and re.match(r'^(eth|e)\d', parts[0], re.I):
                rows.append({'local_port': parts[0], 'remote_host': parts[1],
                             'remote_port': parts[2]})
        return rows or None


class LinuxDriver(DeviceDriver):
    """리눅스 호스트는 지금 도달 확인(Step 2 프로브)까지만 한다.

    SSH로 명령을 돌리려면 클라이언트 의존성이 필요한데, 랩에서 리눅스 노드는
    트래픽 발생용이라 사실 확인 대상이 아니다. 필요해지면 그때 붙인다.
    """

    vendor = 'linux'

    def hostname(self):
        raise DriverError('리눅스 노드는 사실 조회를 지원하지 않습니다.')


DRIVERS = {
    'a10_axapi': A10Driver,
    'arista_eapi': AristaDriver,
    'linux_ssh': LinuxDriver,
}


def get_driver(access):
    """접속 정보로 드라이버를 만든다. 확인 대상이 아니면 None."""
    cls = DRIVERS.get(access.driver)
    if cls is None or not access.mgmt_ip:
        return None
    return cls(access)
