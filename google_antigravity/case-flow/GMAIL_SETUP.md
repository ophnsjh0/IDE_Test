# Gmail 연동 설정 가이드

케이스 메일(A10 / Arista / HPE Aruba / Juniper)을 Gmail에서 자동으로 가져와
Case-Flow에 등록하는 기능의 설정 방법입니다.

## 1. Google Cloud 프로젝트 설정 (최초 1회)

1. https://console.cloud.google.com 접속 → 새 프로젝트 생성 (예: `case-flow`)
2. **API 및 서비스 → 라이브러리** 에서 **Gmail API** 검색 후 **사용 설정**
3. **API 및 서비스 → OAuth 동의 화면**
   - User Type: **외부(External)** 선택 (개인 Gmail 계정용)
   - 앱 이름, 지원 이메일 입력 후 저장
   - **테스트 사용자**에 본인 Gmail 주소(예: ophnsjh0@gmail.com) 추가
4. **API 및 서비스 → 사용자 인증 정보 → 사용자 인증 정보 만들기 → OAuth 클라이언트 ID**
   - 애플리케이션 유형: **데스크톱 앱**
   - 생성 후 JSON 다운로드 → 파일명을 `credentials.json`으로 변경
5. 다운로드한 `credentials.json`을 `backend/` 폴더에 복사

> 회사 Google Workspace 계정으로 전환할 때는 Workspace 관리자 승인 하에
> 내부(Internal) 앱으로 동의 화면을 구성하면 테스트 사용자 등록 없이 사용 가능합니다.
> 코드는 동일하고 `credentials.json` / `token.json`만 교체하면 됩니다.

## 2. 번역용 Claude API 키 설정

메일 본문을 한글로 번역하려면 Anthropic API 키가 필요합니다.

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

- 키가 없으면 번역 없이 원문만 저장됩니다 (동기화 자체는 정상 동작).
- 번역 모델은 기본 `claude-opus-4-8`이며, 비용 절감이 필요하면 변경 가능합니다:

```bash
export CASEFLOW_TRANSLATION_MODEL="claude-haiku-4-5"
```

## 3. 최초 인증 및 동기화 실행

```bash
# 백엔드 폴더 기준이 아니라 프로젝트 루트에서 실행
uv run python backend/manage.py sync_gmail
```

- 최초 실행 시 브라우저가 열리며 Google 로그인/동의 화면이 표시됩니다.
- 동의하면 `backend/token.json`이 생성되고 이후에는 자동 갱신됩니다.
- 웹 화면의 **Gmail 동기화** 버튼(`POST /api/gmail/sync/`)으로도 실행할 수 있습니다.
  (단, 최초 인증은 브라우저를 띄울 수 있는 터미널에서 커맨드로 하는 것을 권장)

## 4. 동작 방식

1. 벤더 도메인(`a10networks.com`, `arista.com`, `hpe.com`, `arubanetworks.com`, `juniper.net`)과
   주고받은 메일 중 미처리 메일을 조회 (시간순으로 처리)
2. 제목에서 벤더 케이스 번호 추출 (`Case #12345`, `SR 5-xxxxx`, `[00123456]` 등)
   - 같은 케이스 번호 또는 같은 메일 스레드 → 기존 케이스에 추가
   - 없으면 새 케이스 자동 생성
3. 메일 1건마다 Claude가 **번역 + 내용 정리**를 한 번에 수행:
   - `summary` / `description`: 첫 메일에서 문제 상황을 정리해 생성 (이후 메일은 덮어쓰지 않음)
   - `action_steps`: `[날짜 시각 수신/발신] 조치 요약` 형식으로 매 메일마다 누적 (타임라인)
   - `resolution`: 해결 내용이 감지된 메일에서 자동으로 채움
   - `status`: 메일 내용에 따라 Open/Pending/Resolved **자동 전환** (수동 변경 가능)
4. 원문(영문)과 한글 번역 전문은 이메일 타임라인에 보존 → "원문 보기" 토글로 확인
5. 처리한 메일에는 Gmail 라벨 `CaseFlow/Processed`가 붙어 중복 처리를 방지
6. API 키가 없거나 분석이 실패하면 원문 그대로 저장하는 폴백으로 동작

### 기존 케이스 재분석

이미 등록된 케이스(메일이 원문 그대로 들어간 케이스 포함)를 저장된 메일로 다시 정리:

```bash
uv run python backend/manage.py reanalyze_cases             # 메일이 있는 전체 케이스
uv run python backend/manage.py reanalyze_cases --case 3    # 특정 케이스만 (id 기준)
```

> 주의: 재분석은 summary/description/action_steps/resolution/status를 다시 생성하므로
> 해당 필드를 수동으로 편집한 내용은 덮어써집니다.

## 5. 주기적 자동 동기화 (선택)

macOS 크론탭으로 5분마다 실행하는 예:

```bash
crontab -e
# 아래 한 줄 추가 (ANTHROPIC_API_KEY는 crontab 안에서 export 필요)
*/5 * * * * cd /Users/junghun/code/IDE_Test/google_antigravity/case-flow && ANTHROPIC_API_KEY="sk-ant-..." uv run python backend/manage.py sync_gmail >> /tmp/caseflow_sync.log 2>&1
```

## 문제 해결

- **"Gmail OAuth 클라이언트 파일이 없습니다"** → `backend/credentials.json` 위치 확인
- **`invalid_grant` 오류** → `backend/token.json` 삭제 후 재인증
- **테스트 앱 토큰 만료(7일)** → OAuth 동의 화면이 "테스트" 상태면 리프레시 토큰이
  7일 후 만료됩니다. 장기 사용 시 동의 화면을 "프로덕션"으로 게시하거나 재인증하세요.
- **번역이 비어 있음** → `ANTHROPIC_API_KEY` 환경변수가 백엔드 서버 실행 셸에 설정되어 있는지 확인
