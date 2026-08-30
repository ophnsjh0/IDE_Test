"""블루프린트 실행 엔진 — 설정을 넣고, 코드로 판정하고, 되돌린다.

세 가지 규칙으로 굴러간다:

1. **적용 원장을 먼저 쓴다.** EVE-NG Community는 스냅샷·롤백 API가 없어 복구가
   전적으로 우리 기록 책임이다. 명령을 보낸 *뒤*에 기록하면 중간에 죽었을 때
   장비에는 들어갔는데 원장에는 없는 찌꺼기가 남는다.
2. **판정은 코드가 한다.** verify는 명령 출력에 무엇이 있어야/없어야 하는지를
   블루프린트에 적어두고, 여기서 문자열로 확인한다. 모델이 "성공했습니다"라고
   말하는 것과 통과한 것은 다르다.
3. **롤백은 대화와 무관하다.** 사람이 화면에서 누를 수 있고, 실행이 죽은 뒤에도
   원장만 있으면 되돌아간다.
"""
import logging

from django.db import transaction
from django.utils import timezone

from api.models import LabAppliedObject, LabRun, LabRunStep
from .lab_drivers import DriverError, get_driver

logger = logging.getLogger(__name__)


class BlueprintError(RuntimeError):
    """블루프린트가 이 랩에서 실행될 수 없는 경우 (역할 미매핑 등)."""


def resolve_roles(accesses):
    """역할 → 접속 정보. 같은 역할이 둘이면 먼저 등록된 것을 쓴다."""
    mapping = {}
    for access in accesses:
        if access.role and access.role not in mapping:
            mapping[access.role] = access
    return mapping


def validate(blueprint, accesses):
    """실행 전에 블루프린트가 이 랩에 맞는지 본다. 문제 목록을 돌려준다."""
    problems = []
    roles = resolve_roles(accesses)
    for i, step in enumerate(blueprint.steps, 1):
        role = step.get('role')
        if not role:
            problems.append(f'{i}단계: role이 없습니다.')
            continue
        access = roles.get(role)
        if access is None:
            problems.append(f"{i}단계: '{role}' 역할을 맡은 노드가 없습니다.")
        elif get_driver(access) is None:
            problems.append(f"{i}단계: '{role}'({access.node_name})에 접속 정보가 없습니다.")
        if not step.get('apply'):
            problems.append(f'{i}단계: apply 명령이 없습니다.')
        if not step.get('rollback'):
            # 되돌릴 방법이 없는 단계는 아예 실행하지 않는다
            problems.append(f'{i}단계: rollback 명령이 없습니다.')
    return problems


class _Recorder:
    """실행 기록을 순번 붙여 남긴다."""

    def __init__(self, run):
        self.run = run
        self.seq = 0

    def step(self, phase, label, status, detail='', node_name=''):
        self.seq += 1
        LabRunStep.objects.create(run=self.run, seq=self.seq, phase=phase,
                                  node_name=node_name, label=label,
                                  status=status, detail=detail[:4000])
        return status


def _verify(driver, spec):
    """검증 하나. 통과/실패와 근거 문구를 돌려준다."""
    command = spec.get('command')
    if not command:
        return False, 'verify에 command가 없습니다.'
    try:
        output = driver.run_command(command)
    except DriverError as e:
        return False, str(e)

    contains = spec.get('contains')
    not_contains = spec.get('not_contains')
    if contains and contains not in output:
        return False, f"'{contains}'가 출력에 없습니다."
    if not_contains and not_contains in output:
        return False, f"'{not_contains}'가 출력에 남아 있습니다."
    if not contains and not not_contains:
        return False, 'verify에 contains/not_contains가 없습니다.'
    return True, f'{command} → 기대값 확인'


def rollback(run):
    """적용 원장을 역순으로 되돌린다. 대화와 무관하게 사람이 부를 수 있다.

    이미 되돌린 항목은 건너뛴다 — 두 번 눌러도 안전해야 한다.
    """
    recorder = _Recorder(run)
    recorder.seq = run.steps.count()
    pending = run.applied.filter(rolled_back_at__isnull=True).order_by('-seq')
    if not pending.exists():
        return 'nothing'

    accesses = {a.node_name: a for a in run.lab.accesses.all()}
    failed = False
    for applied in pending:
        access = accesses.get(applied.node_name)
        driver = get_driver(access) if access else None
        if driver is None:
            recorder.step('rollback', f'{applied.node_name} 되돌리기', 'error',
                          '접속 정보가 없어 되돌릴 수 없습니다.', applied.node_name)
            failed = True
            continue
        try:
            driver.apply(applied.rollback_commands)
        except DriverError as e:
            recorder.step('rollback', f'{applied.node_name} 되돌리기', 'error',
                          str(e), applied.node_name)
            failed = True
            continue
        applied.rolled_back_at = timezone.now()
        applied.save(update_fields=['rolled_back_at'])
        recorder.step('rollback', f'{applied.node_name} 되돌리기', 'pass',
                      ' / '.join(applied.rollback_commands)[:400], applied.node_name)

    run.status = 'error' if failed else 'rolled_back'
    run.finished_at = timezone.now()
    run.save(update_fields=['status', 'finished_at'])
    return 'failed' if failed else 'done'


def execute(run, auto_rollback=True):
    """블루프린트를 실행한다. 실패해도 넣은 것은 되돌린다."""
    blueprint = run.blueprint
    accesses = list(run.lab.accesses.all())
    roles = resolve_roles(accesses)
    recorder = _Recorder(run)

    problems = validate(blueprint, accesses)
    if problems:
        for problem in problems:
            recorder.step('precheck', '블루프린트 확인', 'fail', problem)
        run.status = 'error'
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'finished_at'])
        return run

    recorder.step('precheck', '블루프린트 확인', 'pass',
                  f'{len(blueprint.steps)}단계, 역할 {len(roles)}개 매핑됨')

    ok = True
    applied_seq = 0
    for index, step in enumerate(blueprint.steps, 1):
        access = roles[step['role']]
        driver = get_driver(access)
        label = step.get('label') or f"{index}단계 ({step['role']})"

        # 원장 먼저 — 명령을 보내기 전에 되돌릴 방법을 남긴다
        applied_seq += 1
        with transaction.atomic():
            applied = LabAppliedObject.objects.create(
                run=run, seq=applied_seq, node_name=access.node_name,
                commands=list(step['apply']),
                rollback_commands=list(step['rollback']))

        try:
            driver.apply(step['apply'])
        except DriverError as e:
            recorder.step('apply', label, 'error', str(e), access.node_name)
            ok = False
            break
        recorder.step('apply', label, 'pass',
                      ' / '.join(step['apply'])[:400], access.node_name)

        spec = step.get('verify')
        if not spec:
            recorder.step('verify', label, 'skip', 'verify가 없습니다.', access.node_name)
            continue
        passed, detail = _verify(driver, spec)
        recorder.step('verify', label, 'pass' if passed else 'fail',
                      detail, access.node_name)
        if not passed:
            ok = False
            break

        logger.debug('applied %s on %s', applied.id, access.node_name)

    run.status = 'passed' if ok else 'failed'
    run.save(update_fields=['status'])

    if auto_rollback:
        rollback(run)
        # 롤백이 status를 rolled_back으로 바꾸므로 판정 결과를 되살린다
        run.status = 'passed' if ok else 'failed'
    run.finished_at = timezone.now()
    run.save(update_fields=['status', 'finished_at'])
    return run
