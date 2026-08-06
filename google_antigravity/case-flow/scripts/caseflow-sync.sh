#!/usr/bin/env bash
# VM cron용 Gmail 수집 래퍼. ~/bin/caseflow-sync.sh 로 복사해 crontab에서 호출한다.
#
#   */10 8-19 * * 1-5 /home/case/bin/caseflow-sync.sh
#
# 무료 모델(Gemini)로 분석하고, 실패하면 백엔드가 알아서
# settings.TRANSLATION_FALLBACK_MODELS(기본 claude-haiku-4-5)로 재시도한다.
# --model은 이번 실행에만 적용되므로 웹 UI의 모델 선택은 그대로 유지된다.
#
# --cron: 관리자 페이지(계정 관리)의 "Gmail 자동 수집" 스위치가 꺼져 있으면
# 수집하지 않고 종료한다. 컨테이너에서 호스트 crontab을 못 건드리므로
# 스케줄 자체는 그대로 두고 실행 여부만 DB 값으로 제어한다.
#
# 동기화가 이미 돌고 있으면(웹 버튼과 겹침) 명령이 조용히 정상 종료하므로
# cron 오류 메일이 발생하지 않는다.
set -uo pipefail

PROJECT_DIR="${CASEFLOW_DIR:-$HOME/IDE_Test/google_antigravity/case-flow}"
MODEL="${CASEFLOW_SYNC_MODEL:-gemini-3.5-flash}"
LOG="${CASEFLOW_SYNC_LOG:-$HOME/logs/gmail-sync.log}"
LOG_MAX_LINES=5000
LOG_KEEP_LINES=2000

mkdir -p "$(dirname "$LOG")"
cd "$PROJECT_DIR" || exit 1

# 평문 .env가 없는 평시에는 compose가 POSTGRES_PASSWORD 미설정 경고를 내는데
# (컨테이너에는 이미 값이 들어 있어 무해) 로그를 채우기만 하므로 걸러낸다.
/usr/bin/docker compose exec -T backend python manage.py sync_gmail --cron --model "$MODEL" 2>&1 \
  | grep -v 'POSTGRES_PASSWORD' \
  | while IFS= read -r line; do
      printf '%s %s\n' "$(date '+%F %T')" "$line"
    done >> "$LOG"

if [ "$(wc -l < "$LOG")" -gt "$LOG_MAX_LINES" ]; then
  tail -n "$LOG_KEEP_LINES" "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
