"""읽기 전용 점검 — 장비의 사실과 우리가 아는 것을 대조한다.

판정은 전부 코드가 한다. LLM은 여기 결과를 설명할 뿐 통과/실패를 정하지 않는다.

두 가지를 본다:
1. **hostname 대조** — 등록한 관리 IP로 붙었을 때 나오는 장비 이름이 그 노드
   이름과 맞는가. 랩을 옮기거나 IP를 잘못 적으면 엉뚱한 장비에 설정이 들어간다.
2. **LLDP ↔ EVE-NG 배선 교차검증** — 장비가 보는 이웃과 EVE-NG의 배선이 같은가.
   이게 어긋나면 토폴로지 스냅샷을 믿고 짠 시나리오가 엉뚱한 포트를 건드린다.
"""
import logging
from concurrent.futures import ThreadPoolExecutor

from .lab_drivers import DriverError, get_driver, normalize_port

logger = logging.getLogger(__name__)

PASS = 'pass'
FAIL = 'fail'
SKIP = 'skip'


def _result(check, node, status, detail):
    return {'check': check, 'node': node, 'status': status, 'detail': detail}


def _facts(access):
    """장비 하나에서 hostname과 LLDP 이웃을 읽는다. 실패는 예외 문구로 남긴다."""
    driver = get_driver(access)
    if driver is None:
        return {'access': access, 'skip': '접속 정보가 없거나 확인 대상이 아닙니다.'}
    try:
        hostname = driver.hostname()
    except DriverError as e:
        return {'access': access, 'error': str(e)}
    except NotImplementedError:
        return {'access': access, 'skip': '이 벤더는 사실 조회를 지원하지 않습니다.'}
    try:
        neighbors = driver.lldp_neighbors()
    except DriverError as e:
        # hostname은 읽혔으므로 절반은 살린다
        logger.info('LLDP 조회 실패 %s: %s', access.node_name, e)
        neighbors = None
    return {'access': access, 'hostname': hostname, 'neighbors': neighbors}


def _expected_neighbors(links, node_name):
    """EVE-NG 배선에서 이 노드의 이웃을 뽑는다 — {(로컬포트, 이웃, 이웃포트)}.

    관리망(노드↔네트워크) 연결은 제외한다. LLDP는 장비끼리만 주고받는다.
    """
    expected = set()
    for link in links:
        if link.source_is_network or link.target_is_network:
            continue
        if link.source == node_name:
            expected.add((normalize_port(link.source_port), link.target,
                          normalize_port(link.target_port)))
        elif link.target == node_name:
            expected.add((normalize_port(link.target_port), link.source,
                          normalize_port(link.source_port)))
    return expected


def run_checks(lab, accesses, links):
    """점검을 돌려 결과 목록을 돌려준다. 장비 접속은 병렬로 한다."""
    targets = [a for a in accesses if a.probeable]
    results = []

    unregistered = sorted({n.name for n in lab.nodes.all()}
                          - {a.node_name for a in accesses if a.probeable})
    for name in unregistered:
        results.append(_result('접속 정보', name, SKIP, '관리 IP·계정이 등록되지 않았습니다.'))

    if not targets:
        return results

    with ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
        facts = list(pool.map(_facts, targets))

    for fact in facts:
        access = fact['access']
        name = access.node_name

        if 'skip' in fact:
            results.append(_result('사실 조회', name, SKIP, fact['skip']))
            continue
        if 'error' in fact:
            results.append(_result('사실 조회', name, FAIL, fact['error']))
            continue

        # 1) hostname 대조 — 대소문자와 도메인은 무시한다
        hostname = (fact.get('hostname') or '').split('.')[0]
        if not hostname:
            results.append(_result('장비 확인', name, SKIP, 'hostname을 읽지 못했습니다.'))
        elif hostname.lower() == name.lower():
            results.append(_result('장비 확인', name, PASS, f'hostname={hostname}'))
        else:
            results.append(_result(
                '장비 확인', name, FAIL,
                f'등록된 노드는 {name}인데 {access.mgmt_ip}에 붙으니 {hostname}입니다. '
                '관리 IP가 다른 장비를 가리키고 있습니다.'))

        # 2) LLDP ↔ EVE-NG 배선
        neighbors = fact.get('neighbors')
        if neighbors is None:
            results.append(_result('배선 대조', name, SKIP, 'LLDP 이웃을 읽지 못했습니다.'))
            continue
        seen = {(normalize_port(n['local_port']), n['remote_host'].split('.')[0],
                 normalize_port(n['remote_port'])) for n in neighbors}
        expected = _expected_neighbors(links, name)
        if not expected:
            results.append(_result('배선 대조', name, SKIP, 'EVE-NG에 장비 간 배선이 없습니다.'))
            continue

        missing = expected - seen
        extra = seen - expected
        if not missing and not extra:
            results.append(_result('배선 대조', name, PASS, f'이웃 {len(expected)}개 일치'))
        else:
            parts = []
            if missing:
                parts.append('EVE-NG에는 있는데 장비가 못 봄: '
                             + ', '.join(f'{p}→{h}:{rp}' for p, h, rp in sorted(missing)))
            if extra:
                parts.append('장비는 보는데 EVE-NG에 없음: '
                             + ', '.join(f'{p}→{h}:{rp}' for p, h, rp in sorted(extra)))
            results.append(_result('배선 대조', name, FAIL, ' / '.join(parts)))

    return results


def summarize(results):
    counts = {PASS: 0, FAIL: 0, SKIP: 0}
    for row in results:
        counts[row['status']] = counts.get(row['status'], 0) + 1
    return counts
