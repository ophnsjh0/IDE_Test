# Case Flow

python manage.py search_references "VRRP preempt 동작 조건" --vendor A10
python manage.py search_references "MLAG peer failure" --vendor Arista --full

## Project Structure

- `frontend/`: Next.js frontend application.
- `backend/`: Django REST Framework backend application.

## 전체 실행 방법 (Quick Start)

터미널 2개로 실행합니다. (Gmail/번역 설정은 최초 1회만 필요 — 아래 Gmail 연동 참고)

```bash
# 터미널 1 — 백엔드 (프로젝트 루트에서)
export ANTHROPIC_API_KEY="sk-ant-..."   # ~/.zshrc에 설정했다면 생략
# 0.0.0.0 바인딩 필수 — 빼면 localhost 전용이 되어 사내망 접속이 안 됨
uv run python backend/manage.py runserver 0.0.0.0:8000
# → http://localhost:8000

# 터미널 2 — 프론트엔드
cd frontend
npm run dev
# → http://localhost:3000
```

브라우저에서 http://localhost:3000 접속 → **Gmail 동기화** 버튼으로 케이스 메일을 가져옵니다.

## Gmail 연동

벤더(A10/Arista/HPE Aruba/Juniper) 케이스 메일을 Gmail에서 자동으로 가져와
케이스로 등록합니다. 메일 1건마다 Claude가 한글 번역과 내용 정리를 수행해
케이스 요약/설명/조치 타임라인/해결 내용을 채우고, 상태(Open/Pending/Resolved)를
자동 전환합니다. 원문(영문)은 상세 페이지의 "원문 보기" 토글로 확인할 수 있습니다.

- 설정 방법: [GMAIL_SETUP.md](./GMAIL_SETUP.md)
- 수동 실행: `uv run python backend/manage.py sync_gmail` 또는 웹 UI의 "Gmail 동기화" 버튼
- 기존 케이스 재정리: `uv run python backend/manage.py reanalyze_cases`
- API: `POST /api/gmail/sync/`

## Getting Started

### Prerequisites

- Python 3.13+
- Node.js
- `uv` (Python package manager)

### Backend Setup

1. **Install Dependencies**:
   The project uses `uv` for dependency management.

   ```bash
   uv sync
   ```

2. **Run Migrations** (already done, but good for reference):

   ```bash
   uv run python backend/manage.py migrate
   ```

3. **Start the Development Server**:
   ```bash
   # 0.0.0.0 바인딩 필수 — 빼면 localhost 전용이 되어 사내망 접속이 안 됨
uv run python backend/manage.py runserver 0.0.0.0:8000
   ```
   The backend will start at `http://localhost:8000`.
   - Health Check: `http://localhost:8000/api/health/`
   - Admin: `http://localhost:8000/admin/`

### Frontend Setup

1. Navigate to the frontend directory:

   ```bash
   cd frontend
   ```

2. Install dependencies:

   ```bash
   npm install
   ```

3. Run the development server:
   ```bash
   npm run dev
   ```
