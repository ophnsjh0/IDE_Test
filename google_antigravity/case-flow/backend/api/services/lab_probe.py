"""랩 노드가 실제로 준비됐는지 판정한다.

EVE-NG가 알려주는 status는 QEMU 프로세스가 떴다는 뜻일 뿐이다. 그 안의 OS가
부팅을 마치고 관리 API를 열었는지는 **직접 찔러봐야** 알 수 있다 — A10 vThunder는
8GB/4vCPU라 프로세스가 뜬 뒤에도 수 분간 응답하지 않는다.

여기서 하는 건 "살아 있나"까지다. 설정을 읽거나 바꾸는 드라이버는 Step 3에서
별도로 만든다.
"""
import logging
import socket
from concurrent.futures import ThreadPoolExecutor

import requests
import urllib3

logger = logging.getLogger(__name__)

# 프로브는 화면 폴링이 매번 부르는 자리라 짧게 끊는다. 장비가 부팅 중이면
# 어차피 응답하지 않고, 다음 폴링에서 다시 본다.
PROBE_TIMEOUT = 3
MAX_PARALLEL = 8

# 노드 상태 — EVE-NG의 running과 프로브 결과를 합쳐 정한다
OFF = 'off'          # EVE-NG: 프로세스 없음
BOOTING = 'booting'  # 프로세스는 떴는데 관리 API 무응답
READY = 'ready'      # 관리 API가 응답
UNKNOWN = 'unknown'  # 프로세스는 떴는데 접속 정보가 없어 확인할 수 없음

# 랩 장비는 자체 서명 인증서를 쓴다. 랩 안에서만 도는 프로브라 검증하지 않고,
# 대신 경고를 끄지 않으면 폴링마다 로그가 쌓인다.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _probe_a10(access):
    """aXAPI 인증이 통과하면 준비된 것으로 본다.

    A10은 SSH CLI 스크래핑이 불안정해서(enable이 산발적으로만 잡히고 배너가
    끼어든다) aXAPI가 사실상 유일한 경로다.
    """
    res = requests.post(
        f'https://{access.mgmt_ip}/axapi/v3/auth',
        json={'credentials': {'username': access.username, 'password': access.password}},
        timeout=PROBE_TIMEOUT, verify=False)
    return res.status_code == 200 and 'signature' in res.text


def _probe_arista(access):
    """eAPI에 show version을 한 번 던져본다.

    함정: Management1이 vrf mgmt에 있으면 eAPI를 mgmt VRF에 바인딩해야 관리
    IP로 붙는다(management api http-commands -> vrf mgmt -> no shutdown).
    바인딩이 안 돼 있으면 포트 자체가 열리지 않아 여기서 계속 실패한다.
    """
    res = requests.post(
        f'http://{access.mgmt_ip}/command-api',
        json={'jsonrpc': '2.0', 'method': 'runCmds',
              'params': {'version': 1, 'cmds': ['show version'], 'format': 'json'},
              'id': 'caseflow-probe'},
        auth=(access.username, access.password), timeout=PROBE_TIMEOUT)
    return res.status_code == 200 and 'result' in res.json()


def _probe_tcp22(access):
    """리눅스 호스트는 SSH 포트가 열렸는지로 본다.

    ping을 쓰지 않는 이유: 컨테이너에서 ICMP를 보내려면 권한이 필요하고,
    ping이 되는 것과 로그인할 수 있는 것은 다르다.
    """
    with socket.create_connection((access.mgmt_ip, 22), timeout=PROBE_TIMEOUT):
        return True


PROBES = {
    'a10_axapi': _probe_a10,
    'arista_eapi': _probe_arista,
    'linux_ssh': _probe_tcp22,
}


def probe(access):
    """접속 정보 하나로 준비 여부를 본다. 실패 원인은 구분하지 않는다 —
    부팅 중이든 설정이 틀렸든 '아직 아니다'는 같기 때문."""
    handler = PROBES.get(access.driver)
    if handler is None or not access.mgmt_ip:
        return None  # 확인할 수 없음
    try:
        return bool(handler(access))
    except Exception as e:
        logger.debug('probe failed %s(%s): %s', access.node_name, access.driver, e)
        return False


def node_states(running_by_name, accesses):
    """{노드 이름: 상태}. running은 EVE-NG에서, ready는 프로브에서 온다.

    프로브는 노드마다 최대 PROBE_TIMEOUT초 걸리므로 병렬로 돌린다 — 9노드를
    직렬로 찌르면 폴링 한 번에 27초가 된다.
    """
    by_name = {a.node_name: a for a in accesses}
    # 꺼진 노드는 찌르지 않는다 (타임아웃만 기다리게 된다)
    targets = [by_name[name] for name, running in running_by_name.items()
               if running and name in by_name and by_name[name].probeable]

    results = {}
    if targets:
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(targets))) as pool:
            for access, ok in zip(targets, pool.map(probe, targets)):
                results[access.node_name] = ok

    states = {}
    for name, running in running_by_name.items():
        if not running:
            states[name] = OFF
        elif name not in results:
            # 프로세스는 떴는데 접속 정보가 없어 준비 여부를 알 수 없다.
            # '기동 중'으로 뭉뚱그리면 영영 안 끝나는 것처럼 보이므로 구분한다.
            states[name] = UNKNOWN
        else:
            states[name] = READY if results[name] else BOOTING
    return states
