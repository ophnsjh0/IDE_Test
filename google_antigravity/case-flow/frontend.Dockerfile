# CaseFlow 프론트엔드 이미지 — Next.js 프로덕션 빌드 (standalone)
# 빌드 컨텍스트: case-flow 루트 (docker-compose.yml 참고)
FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend .
# NEXT_PUBLIC_API_BASE는 빌드 시점에 번들에 박힌다.
# 비워두면 런타임에 "접속한 호스트:8000"으로 자동 추론 (lib/api.ts).
ARG NEXT_PUBLIC_API_BASE
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=3000
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
CMD ["node", "server.js"]
