"""검증된 명령 사전 — 랩에서 실제로 돌려본 명령 묶음을 (벤더, OS 버전)으로 모은다.

왜 필요한가: 실기기 검증에서 모델이 고른 EOS 명령이 그 버전에 없어 verify가
실패하고 자동 롤백된 일이 있었다. 설계가 걸러내긴 했지만, 걸러내는 것과 다음에
안 틀리는 것은 다르다. 이 사전은 그 다음을 맡는다.

세 가지 규칙:

1. **키에 버전이 들어간다.** 벤더만으로는 부족하다 — 실패의 원인이 버전이었다.
2. **랩에 매이지 않는다.** 랩별로 두면 새 랩마다 사전이 비어 같은 실패를
   반복한다. 대신 어느 실행에서 확인됐는지를 항목에 붙여 추적 가능하게 한다.
3. **실패도 남긴다.** "이 버전엔 이 명령이 없다"가 실은 더 쓸모 있다.
"""
import hashlib
import json
import logging

from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When

from api.models import LabCommandRecipe, LabNodeFact
from .lab_drivers import get_driver

logger = logging.getLogger(__name__)

# 드라이버는 곧 벤더다. lab 쪽 코드 여러 곳이 같은 표를 봐야 해서 여기 둔다.
DRIVER_VENDOR = {'a10_axapi': 'A10', 'arista_eapi': 'Arista'}


def fingerprint(commands):
    """명령 묶음의 지문. 같은 묶음을 몇 번 돌려도 사전에 한 행이어야 한다.

    앞뒤 공백과 대소문자만 정규화한다. 그 이상 손대면(공백 접기, 인자 제거)
    서로 다른 명령이 한 항목으로 합쳐질 수 있다.
    """
    normalized = [c.strip().lower() for c in commands if c and c.strip()]
    blob = json.dumps(normalized, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def _text(value, limit=100):
    """드라이버가 준 값을 그대로 믿지 않는다.

    벤더 API의 응답 모양은 버전마다 다르고, 여기서 문자열이 아닌 것이
    넘어오면 사전의 버전 축에 쓰레기가 들어간다. 문자열이 아니면 없는 것으로
    친다 — 짐작한 값보다 빈 값이 낫다.
    """
    return value.strip()[:limit] if isinstance(value, str) else ''


def build_search_text(purpose, commands, verify_command=''):
    """검색용 평문. 명령이 JSONField라 DB 키워드 검색이 안 되므로 따로 둔다."""
    return '\n'.join([purpose, *commands, verify_command]).strip()


def observe_node(access, node=None, driver=None):
    """장비에 붙어 OS 버전·모델을 읽어 LabNodeFact에 남긴다.

    못 읽으면 EVE-NG의 이미지 문자열로 갈음하되 source='image'로 표시한다 —
    짐작한 값을 확인된 것처럼 쓰면 사전의 버전 축이 조용히 어긋난다.
    실패해도 예외를 올리지 않는다. 버전을 못 읽는 것이 실행을 막을 이유는 없다.

    driver를 받는 이유: 호출자가 이미 만들어 쓰고 있으면 그것을 그대로 쓴다.
    여기서 따로 만들면 접속이 한 번 더 나가고, 무엇보다 호출자가 붙어 있는
    장비와 다른 것을 볼 수 있다.
    """
    version, model, source = '', '', 'probe'
    if driver is None:
        driver = get_driver(access)
    if driver is not None:
        try:
            facts = driver.device_facts() or {}
            version = _text(facts.get('os_version'))
            model = _text(facts.get('device_model'))
        except Exception:   # 드라이버 오류·응답 파싱 실패 모두 — 부가 정보다
            logger.info('device_facts failed for %s', access.node_name, exc_info=True)
    if not version and node is not None:
        # EVE-NG가 아는 것은 이미지 파일 이름뿐이다 (예: veos-4.28.0F)
        version = _text(node.image) or _text(node.template)
        source = 'image'
    if not version:
        return None

    fact, _ = LabNodeFact.objects.update_or_create(
        lab=access.lab, node_name=access.node_name,
        defaults={'os_version': version, 'device_model': model, 'source': source})
    return fact


def record(step, access, fact, run, passed, detail=''):
    """블루프린트 한 단계의 결과를 사전에 반영한다.

    한 단계가 그대로 한 항목이다 — 넣고(apply) 확인하고(verify) 되돌리는
    (rollback) 한 세트가 '목적별 묶음'의 자연스러운 단위라서.

    실패해도 예외를 올리지 않는다. 사전은 부가 기능이고, 여기서 터져서
    실행이 멈추면 장비에 넣은 설정이 되돌아가지 않을 수 있다.
    """
    try:
        return _record(step, access, fact, run, passed, detail)
    except Exception:
        logger.exception('recipe recording failed (run %s)', getattr(run, 'id', None))
        return None


def _record(step, access, fact, run, passed, detail):
    vendor = DRIVER_VENDOR.get(access.driver)
    if vendor is None:
        return None
    commands = list(step.get('apply') or [])
    if not commands:
        return None
    verify = step.get('verify') or {}
    key = fingerprint(commands)

    with transaction.atomic():
        recipe = (LabCommandRecipe.objects
                  .select_for_update()
                  .filter(vendor=vendor, os_version=(fact.os_version if fact else ''),
                          fingerprint=key).first())
        if recipe is None:
            recipe = LabCommandRecipe(
                vendor=vendor, os_version=(fact.os_version if fact else ''),
                fingerprint=key, verified_count=0, failed_count=0)
        recipe.purpose = (step.get('label') or step.get('role') or '이름 없는 단계')[:200]
        recipe.apply_commands = commands
        recipe.verify_command = (verify.get('command') or '')[:300]
        recipe.verify_contains = (verify.get('contains') or '')[:300]
        recipe.verify_not_contains = (verify.get('not_contains') or '')[:300]
        recipe.rollback_commands = list(step.get('rollback') or [])
        recipe.device_model = (fact.device_model if fact else '')[:100]
        recipe.search_text = build_search_text(recipe.purpose, commands,
                                               recipe.verify_command)
        recipe.source = 'run'
        recipe.last_run = run
        if passed:
            recipe.verified_count += 1
        else:
            recipe.failed_count += 1
            recipe.last_failure = (detail or '')[:4000]
        # 한 번이라도 통과했으면 검증된 것으로 둔다. 나중에 같은 버전에서
        # 실패해도 그 사실은 failed_count와 last_failure에 남는다.
        recipe.outcome = 'verified' if recipe.verified_count else 'failed'
        recipe.save()
    return recipe


def search(vendor='', os_version='', query='', outcome='', limit=10):
    """사전 조회. 에이전트 도구와 화면이 같은 함수를 쓴다.

    버전은 접두어로 맞춘다 — 장비는 '4.28.0F'라고 말하는데 사람은 '4.28'로
    묻는다. 버전을 모르는 항목('')도 함께 낸다: 벤더만 맞아도 없는 것보다 낫고,
    결과에 os_version이 그대로 실려 있어 읽는 쪽이 판단할 수 있다.
    """
    items = LabCommandRecipe.objects.all()
    if vendor:
        items = items.filter(vendor=vendor)
    if os_version:
        items = items.filter(Q(os_version__startswith=os_version) | Q(os_version=''))
    if outcome:
        items = items.filter(outcome=outcome)
    for keyword in (query or '').split():
        items = items.filter(search_text__icontains=keyword)
    # 증거가 센 것부터: 검증됨 > 미검증(직접 등록) > 실패.
    # outcome 문자열을 그대로 정렬하면 'failed'가 먼저 나온다 — 사전을 읽는
    # 쪽이 가장 먼저 보는 것이 실패한 명령이면 곤란하다.
    ranked = items.annotate(rank=Case(
        When(outcome='verified', then=Value(0)),
        When(outcome='untested', then=Value(1)),
        default=Value(2), output_field=IntegerField()))
    return list(ranked.order_by('rank', '-verified_count', '-updated_at')[:limit])


def to_dict(recipe):
    return {
        'id': recipe.id,
        'vendor': recipe.vendor,
        'os_version': recipe.os_version or '',
        'purpose': recipe.purpose,
        'apply': recipe.apply_commands,
        'verify': {'command': recipe.verify_command,
                   'contains': recipe.verify_contains,
                   'not_contains': recipe.verify_not_contains},
        'rollback': recipe.rollback_commands,
        'outcome': recipe.outcome,
        'verified_count': recipe.verified_count,
        'failed_count': recipe.failed_count,
        'last_failure': recipe.last_failure,
        'device_model': recipe.device_model,
        'source': recipe.source,
        'last_run': recipe.last_run_id,
    }
