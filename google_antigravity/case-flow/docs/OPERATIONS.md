# CaseFlow VM 운영 가이드

운영 VM: `192.168.74.158` (`case` 계정), 프로젝트 경로 `~/IDE_Test/google_antigravity/case-flow`

구성: docker compose — db(PostgreSQL 16) + backend(Django, :8000) + frontend(Next.js, :3000)

## 보안 구성 요약 (2026-07-16 적용)

| 항목 | 방식 |
|---|---|
| `.env` (API 키 등) | `.env.age`로 암호화 보관. 기동 순간에만 복호화, 즉시 파기 |
| Gmail OAuth (credentials/token.json) | `gmail_secrets` docker 볼륨(`/app/secrets/`, 호스트 root 전용). 암호문 백업 `~/gmail-secrets.tar.gz.age` |
| DB 백업 | cron 매일 03:00 `pg_dump` → age 공개키 암호화 → `~/backups/`, 03:30 14일 초과분 삭제 |
| 백업 복호화 키 | **PC의 `~/keys/caseflow-backup.key`에만 존재** (VM에는 공개키만) |

한계: VM root 권한자는 `docker inspect`·볼륨 접근으로 평문 조회 가능. 목적은 일반 사용자·파일 유출 경로에서의 평문 제거.

## 철칙

1. **컨테이너를 새로 만드는 명령(`up`, recreate)은 오직 `~/bin/caseflow-up.sh`로.**
   평문 `.env` 없이 `docker compose up`을 직접 돌리면 API 키가 빈 값인 컨테이너가 만들어진다.
   조회·실행 명령(`ps` `logs` `exec` `cp`)은 자유 — 이때 나오는 `POSTGRES_PASSWORD not set` WARN은 무해.
2. **`docker compose down -v` 절대 금지.** `-v`가 볼륨을 지워 DB 전체(pgdata)와 Gmail OAuth(gmail_secrets)가 한 번에 유실된다. 내릴 일이 있으면 `-v` 없이.
3. **암호문 백업 검증 전에 평문을 파기하지 않는다.**
4. 잃어버리면 복구 불가능한 것 3가지 — 비밀번호 관리자에 보관:
   ① `.env.age` 패스프레이즈 ② `gmail-secrets.tar.gz.age` 패스프레이즈 ③ PC의 `~/keys/caseflow-backup.key` 내용

## 기동 스크립트 `~/bin/caseflow-up.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
cd ~/IDE_Test/google_antigravity/case-flow
[ -f .env ] && shred -u .env      # 잔존 평문 제거 (age는 기존 파일을 덮어쓰지 않음)
age -d -o .env .env.age           # 패스프레이즈 입력
docker compose up -d --build "$@"
shred -u .env                     # 기동 후 평문 즉시 파기
```

`docker compose up`은 실행 순간에만 `.env`를 읽어 컨테이너 환경변수로 넣으므로, 직후 평문을 지워도 컨테이너·재시작·재부팅에 영향 없다.

---

## 케이스 1 — 기능 추가 후 배포

```bash
# PC: 개발 → 커밋 → git push

# VM:
cd ~/IDE_Test/google_antigravity/case-flow
git pull
~/bin/caseflow-up.sh backend     # 백엔드만 바뀐 경우
~/bin/caseflow-up.sh frontend    # 프론트만 바뀐 경우
~/bin/caseflow-up.sh             # 양쪽 다 / compose 파일이 바뀐 경우
```

- DB 마이그레이션은 backend 시작 시 자동(`migrate --noinput`).
- 재생성해도 `pgdata`·`gmail_secrets` 볼륨은 유지 — 재주입 불필요.
- 확인: `docker compose ps` → 이상 시 `docker compose logs -f backend`

## 케이스 2 — `.env` 수정 (API 키 교체 등)

```bash
cd ~/IDE_Test/google_antigravity/case-flow
age -d -o .env .env.age                  # 복호화
vi .env                                  # 수정
rm .env.age && age -p -o .env.age .env   # 재암호화 (패스프레이즈 변경 가능)
~/bin/caseflow-up.sh backend             # 적용 + 평문 파기
```

⚠️ `POSTGRES_PASSWORD`는 이 방법으로 바꾸면 안 된다. DB 실제 비밀번호는 최초 initdb 때 고정된 것이라 env만 바꾸면 접속 불일치로 backend가 죽는다. 변경 시 `ALTER USER` 절차 필요.

공용 API 키 전환 시: 위 절차로 교체 후 **개인 키를 콘솔에서 반드시 revoke**.

## 케이스 3a — DB 백업 복구

```bash
# PC: 복호화 (개인키는 PC에만 있음)
scp case@192.168.74.158:backups/caseflow-YYYY-MM-DD.sql.age .
age -d -i ~/keys/caseflow-backup.key caseflow-YYYY-MM-DD.sql.age > restore.sql
scp restore.sql case@192.168.74.158:
rm restore.sql caseflow-YYYY-MM-DD.sql.age   # PC 정리

# VM: ⚠️ 현재 DB가 백업 시점으로 통째 교체됨 — 실행 전 수동 백업 권장 (아래 cron 라인 참고)
cd ~/IDE_Test/google_antigravity/case-flow
docker compose stop backend
docker compose exec db psql -U caseflow -d postgres -c "DROP DATABASE caseflow;"
docker compose exec db psql -U caseflow -d postgres -c "CREATE DATABASE caseflow OWNER caseflow;"
docker compose exec -T db psql -U caseflow caseflow < ~/restore.sql
shred -u ~/restore.sql
~/bin/caseflow-up.sh backend
```

## 케이스 3b — Gmail OAuth 복구 (볼륨 유실·토큰 파손)

```bash
cd ~/IDE_Test/google_antigravity/case-flow
age -d ~/gmail-secrets.tar.gz.age | tar xzf -    # backend/ 아래로 풀림
docker cp backend/credentials.json case-flow-backend-1:/app/secrets/credentials.json
docker cp backend/token.json case-flow-backend-1:/app/secrets/token.json
docker exec case-flow-backend-1 python manage.py sync_gmail   # 검증
shred -u backend/credentials.json backend/token.json          # 평문 재파기
```

공용 Gmail 계정 전환 시에도 같은 방식: 새 credentials/token을 `docker cp`로 덮어쓰면 된다.
(교체 후 암호문 백업도 갱신: `tar czf - backend/credentials.json backend/token.json | age -p -o ~/gmail-secrets.tar.gz.age` — 단, 평문은 주입·검증 후 파기)

## 케이스 4 — 리포트 템플릿 교체

`backend/report_templates/`는 바인드 마운트 + 해시 캐싱이라 **같은 파일명으로 덮어쓰기만 하면 즉시 적용**. 재빌드·재기동 불필요.

```bash
cp 새템플릿.docx ~/IDE_Test/google_antigravity/case-flow/backend/report_templates/TAC_CaseReport_DOC_Template.docx
```

---

## 백업 cron (등록 완료 상태)

```cron
0 3 * * * cd /home/case/IDE_Test/google_antigravity/case-flow && /usr/bin/docker compose exec -T db pg_dump -U caseflow caseflow | /usr/bin/age -r age1v53ducmhgvqxfkapq7nce80v5fgxn0t7ypfm8hm9cyjeagcxn4fsg57vuj -o /home/case/backups/caseflow-$(date +\%F).sql.age 2>> /home/case/backups/cron.log
30 3 * * * find /home/case/backups -name 'caseflow-*.sql.age' -mtime +14 -delete
```

- 공개키(`age1v53...`)는 노출돼도 무해 — 암호화만 가능, 복호화는 PC 개인키로만.
- 동작 확인: `ls -la ~/backups/` (날짜별 `.sql.age`), 실패 시 `~/backups/cron.log` 확인.

## 기타

- VM 재부팅: 조치 불필요 (`restart: unless-stopped`, env는 컨테이너에 유지).
- `NEXT_PUBLIC_API_BASE`는 프론트 빌드 시점에 번들에 박힘 — API 주소 고정이 필요하면 compose의 build args 주석 해제 후 frontend 리빌드.
- DB 관리: Django admin(`:8000/admin/`, superuser는 `docker compose exec backend python manage.py createsuperuser`) 또는 `docker compose exec db psql -U caseflow caseflow`.
