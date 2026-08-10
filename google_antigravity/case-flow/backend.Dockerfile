# CaseFlow 백엔드 이미지 — Python 3.13 + uv
# 빌드 컨텍스트: case-flow 루트 (docker-compose.yml 참고)
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

# 의존성 레이어 캐싱: 소스보다 먼저 복사
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY backend ./backend

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app/backend

# DEBUG=0에서 admin 정적 파일을 whitenoise로 서빙하기 위한 수집
RUN python manage.py collectstatic --noinput

# 기동 시 마이그레이션 후 gunicorn 실행.
#
# --timeout 300: 리포트 생성 등 장시간 에이전트 호출 대비.
# workers x threads = 동시 처리 슬롯. AI 응답(20~60초)·문서 임베딩(수십 분)이
# 슬롯을 통째로 점유하는 구조라, 슬롯이 마르면 케이스 목록 같은 단순 조회까지
# 뒤에서 대기한다. 4x8=32로 여유를 둔다 (2026-08-10, 기존 2x4=8).
#   - 스레드를 늘리는 게 효율적: AI 호출은 대부분 외부 API 대기(I/O)라 GIL을 놓는다
#   - 메모리: 워커마다 벡터 검색 행렬을 따로 캐시한다 (실측 워커당 ~135MB,
#     4워커 ~540MB / VM RAM 7.8GB). 문서가 늘면 이 값도 함께 커진다
#   - DB: CONN_MAX_AGE=0이라 커넥션은 요청 단위로 반납된다
#     (최대 동시 32개 < Postgres max_connections 100)
# 값 조정은 이미지 재빌드 없이 .env의 GUNICORN_WORKERS/THREADS로도 가능.
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn config.wsgi -b 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-4} --threads ${GUNICORN_THREADS:-8} --timeout 300"]
