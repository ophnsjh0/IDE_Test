#!/bin/bash
# 백엔드 실행 스크립트 — 사내망 접속을 위해 0.0.0.0 바인딩 고정.
# 사용법: ./run-backend.sh
cd "$(dirname "$0")"
exec uv run python backend/manage.py runserver 0.0.0.0:8000
