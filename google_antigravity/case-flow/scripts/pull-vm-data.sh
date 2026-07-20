#!/usr/bin/env bash
# VM 운영 DB(Postgres)를 로컬 개발 DB(SQLite)로 가져온다.
# 사용법: scripts/pull-vm-data.sh
# - 기존 로컬 DB는 backend/db.sqlite3.bak-<날짜>로 백업됨
# - 덤프 파일(개인정보 포함)은 로컬/VM/컨테이너 모두에서 삭제됨
set -euo pipefail

VM="case@192.168.74.158"
VM_DIR="~/IDE_Test/google_antigravity/case-flow"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$REPO_ROOT/backend"
PYTHON="$REPO_ROOT/.venv/bin/python"
DUMP_LOCAL="$(mktemp -t caseflow_dump).json"

# 비밀번호를 한 번만 입력하도록 SSH 연결 재사용
CTRL="/tmp/caseflow-ssh-$$"
SSH_OPTS=(-o ControlMaster=auto -o ControlPath="$CTRL" -o ControlPersist=120)
cleanup() {
  rm -f "$DUMP_LOCAL"
  ssh "${SSH_OPTS[@]}" -O exit "$VM" 2>/dev/null || true
}
trap cleanup EXIT

echo "==> VM에서 덤프 생성 중..."
ssh "${SSH_OPTS[@]}" "$VM" "cd $VM_DIR && \
  docker compose exec -T backend python manage.py dumpdata \
    --natural-foreign --natural-primary \
    -e contenttypes -e auth.permission -e admin.logentry -e sessions \
    -o /tmp/dump.json && \
  docker compose cp backend:/tmp/dump.json /tmp/dump.json"

echo "==> 로컬로 복사 중..."
scp "${SSH_OPTS[@]}" "$VM:/tmp/dump.json" "$DUMP_LOCAL"

echo "==> VM 쪽 덤프 삭제..."
ssh "${SSH_OPTS[@]}" "$VM" "rm -f /tmp/dump.json && cd $VM_DIR && \
  docker compose exec -T backend rm -f /tmp/dump.json"

echo "==> 로컬 DB 백업 후 재생성..."
BAK="$BACKEND/db.sqlite3.bak-$(date +%Y%m%d-%H%M%S)"
[ -f "$BACKEND/db.sqlite3" ] && mv "$BACKEND/db.sqlite3" "$BAK" && echo "    백업: $BAK"
(cd "$BACKEND" && "$PYTHON" manage.py migrate --no-input >/dev/null && \
  "$PYTHON" manage.py loaddata "$DUMP_LOCAL")

echo "==> 결과 확인"
(cd "$BACKEND" && "$PYTHON" manage.py shell -c "
from api.models import Case, CaseEmail
print('cases:', Case.objects.count(), '/ emails:', CaseEmail.objects.count())
print('latest email:', CaseEmail.objects.order_by('-received_at').values_list('received_at', flat=True).first())
" 2>/dev/null | grep -v 'objects imported')

echo "완료. 이전 백업(db.sqlite3.bak-*)은 필요 없어지면 삭제하세요."
