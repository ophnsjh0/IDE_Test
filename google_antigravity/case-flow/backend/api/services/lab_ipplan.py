"""랩 IP 설계 — 어디를 쓸 수 있고, 지금 무엇이 겹치는가.

**시스템은 배정하지 않고 경고만 한다.** 사람이 고른 IP를 막지 않는 이유:
랩은 일부러 이상한 값을 넣어보는 곳이기도 하고, 자동 배정은 사람이 장비 안에
이미 넣어둔 설정과 어긋나는 순간 더 큰 혼란이 된다. 대신 겹쳤거나 풀 밖이면
그렇다고 말해준다.

**기준은 랩이 아니라 서버 전체다.** EVE-NG Community에서는 랩이 한 서버에
평면으로 놓이고 관리망(pnet0)을 공유해서, 옆 랩이 쓰는 IP는 이 랩이 못 쓴다.
"""
import ipaddress
import logging

from api.models import Lab, LabIpPool, LabNodeAccess

logger = logging.getLogger(__name__)

# 우리 랩 서버의 기본값. 화면에서 바꿀 수 있고, 여기 값은 처음 한 번만 쓰인다.
# 관리망은 사내망에서 떼어 받은 자리라 연속이 아니다 — 구멍이 그대로 남아 있다.
DEFAULT_MGMT = {
    'cidr': '192.168.74.0/24',
    'gateway': '192.168.74.1',
    'ranges': ['192.168.74.139-192.168.74.140',
               '192.168.74.150-192.168.74.157',
               '192.168.74.159'],
    'note': '사내망에서 받은 자리. 연속이 아니라 조각으로 적는다.',
}
DEFAULT_DATA = {
    'cidr': '172.16.0.0/12',
    'lab_prefix': 24,
    'note': '랩 안에서만 도는 시험 트래픽 대역. 랩마다 /24를 하나씩 떼어 준다.',
}


def ensure_defaults(server):
    """이 서버에 풀 정의가 없으면 기본값으로 하나 만들어 둔다 (한 번만)."""
    LabIpPool.objects.get_or_create(server=server, kind='mgmt',
                                    defaults=DEFAULT_MGMT)
    LabIpPool.objects.get_or_create(server=server, kind='data',
                                    defaults=DEFAULT_DATA)
    return {p.kind: p for p in server.ip_pools.all()}


def parse_ranges(ranges):
    """["a-b", "c"] → 주소 집합. 못 읽는 조각은 건너뛴다.

    풀 정의가 조금 틀렸다고 경고 기능 전체가 죽으면 안 된다 — 이건 사람이
    IP를 적는 것을 돕는 장치이지, 막는 장치가 아니다.
    """
    addresses = set()
    for chunk in ranges or []:
        text = str(chunk).strip()
        if not text:
            continue
        try:
            if '-' in text:
                start, end = (part.strip() for part in text.split('-', 1))
                first, last = ipaddress.ip_address(start), ipaddress.ip_address(end)
                if int(last) - int(first) > 4096:
                    # 실수로 큰 범위를 적었을 때 메모리를 통째로 먹지 않게
                    logger.warning('ip range too large, skipped: %s', text)
                    continue
                addresses.update(ipaddress.ip_address(n)
                                 for n in range(int(first), int(last) + 1))
            else:
                addresses.add(ipaddress.ip_address(text))
        except ValueError:
            logger.info('unreadable ip range chunk: %s', text)
    return addresses


def used_ips(server, exclude_lab=None):
    """이 서버에서 이미 쓰이는 관리 IP → {ip 문자열: [어느 랩/노드]}.

    exclude_lab: 지금 편집 중인 랩. 자기 자신과 겹친다고 경고하면 저장할 때마다
    시끄럽기만 하다 — 같은 랩 안의 중복은 따로 본다.
    """
    rows = (LabNodeAccess.objects
            .filter(lab__server=server).exclude(mgmt_ip='')
            .select_related('lab'))
    if exclude_lab is not None:
        rows = rows.exclude(lab=exclude_lab)
    taken = {}
    for access in rows:
        taken.setdefault(access.mgmt_ip.strip(), []).append(
            f'{access.lab.name}/{access.node_name}')
    return taken


def check_assignments(lab, rows):
    """사람이 적은 관리 IP를 보고 경고 목록을 만든다.

    rows: [{'node_name', 'mgmt_ip'}]. 저장 전에도 후에도 같은 함수로 본다.
    """
    pools = ensure_defaults(lab.server)
    mgmt = pools.get('mgmt')
    allowed = parse_ranges(mgmt.ranges) if mgmt else set()
    taken = used_ips(lab.server, exclude_lab=lab)

    warnings = []
    seen = {}
    for row in rows:
        raw = (row.get('mgmt_ip') or '').strip()
        node = row.get('node_name') or '?'
        if not raw:
            continue
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            warnings.append({'node': node, 'ip': raw,
                             'message': 'IP 주소 형식이 아닙니다.'})
            continue

        # 1) 서버 안의 다른 랩과 겹치는가 — pnet0을 공유하므로 실제로 충돌한다
        if raw in taken:
            warnings.append({
                'node': node, 'ip': raw,
                'message': f"이미 {', '.join(taken[raw])}이(가) 쓰고 있습니다."})
        # 2) 같은 랩 안에서 두 번 적었는가
        if raw in seen:
            warnings.append({'node': node, 'ip': raw,
                             'message': f'{seen[raw]}와(과) 같은 IP입니다.'})
        seen[raw] = node

        # 3) 풀 밖인가 — 막지는 않는다. 일부러 밖을 쓰는 시험도 있다.
        if allowed and address not in allowed:
            warnings.append({'node': node, 'ip': raw,
                             'message': '관리 IP 풀 밖의 주소입니다.'})
    return warnings


def free_mgmt_ips(server, limit=20):
    """아직 아무도 안 쓰는 관리 IP. 사람이 고를 때 보여주려고 낸다."""
    pools = ensure_defaults(server)
    mgmt = pools.get('mgmt')
    if mgmt is None:
        return []
    taken = set(used_ips(server))
    free = [str(a) for a in sorted(parse_ranges(mgmt.ranges))
            if str(a) not in taken]
    return free[:limit]


def suggest_data_subnet(server, lab=None):
    """아직 아무 랩도 안 쓰는 시험 트래픽 대역 하나.

    랩끼리 대역이 겹치면 서버를 공유하는 구조상 트래픽이 섞인다. 172.16/12는
    100만 주소짜리라 그대로는 못 쓰고, 랩마다 lab_prefix 크기로 떼어 준다.
    """
    pools = ensure_defaults(server)
    pool = pools.get('data')
    if pool is None or not pool.cidr:
        return ''
    try:
        supernet = ipaddress.ip_network(pool.cidr, strict=False)
        subnets = supernet.subnets(new_prefix=pool.lab_prefix)
    except ValueError:
        logger.info('unreadable data pool cidr: %s', pool.cidr)
        return ''

    used = set(Lab.objects.filter(server=server).exclude(data_subnet='')
               .exclude(id=getattr(lab, 'id', None))
               .values_list('data_subnet', flat=True))
    for subnet in subnets:
        if str(subnet) not in used:
            return str(subnet)
    return ''


def to_dict(pool):
    return {
        'kind': pool.kind,
        'cidr': pool.cidr,
        'gateway': pool.gateway,
        'ranges': pool.ranges,
        'lab_prefix': pool.lab_prefix,
        'note': pool.note,
    }
