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
# --timeout 300: 리포트 생성 등 장시간 에이전트 호출 대비
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn config.wsgi -b 0.0.0.0:8000 --workers 2 --threads 4 --timeout 300"]
