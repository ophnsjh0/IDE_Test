# 레퍼런스 문서 임베딩 (벡터 검색) 가이드

벤더 공식 문서(config guide 등)를 벡터 검색 가능하게 만들어, AI 도우미
기술지원 에이전트가 웹 검색 전에 **사내 보관 공식 문서를 먼저 인용**하도록
하는 기능. 2026-07-20 구축.

## 동작 원리 (전체 흐름)

```
[인제스트 — 문서 추가/변경 시 1회]
backend/reference_docs/<벤더>/*.pdf
  → ① 텍스트 추출 (pypdf, 로컬)
  → ② 청킹: ~4,000자(≈1,000토큰) 단위, 600자 오버랩, 페이지 범위 기록 (로컬)
  → ③ 청크 텍스트를 OpenAI 임베딩 API로 전송 → 1,536차원 숫자 벡터로 변환
  → ④ DB 저장: 청크 원문 + 벡터(float32 바이너리) + 사용 모델명

[검색 — 질문마다]
질문 → 같은 모델로 임베딩(벡터 1개) → DB의 전체 청크 벡터와
코사인 유사도 비교(numpy, 로컬, 수십 ms) → 상위 청크 반환
```

핵심 포인트:

- **청킹은 로컬에서** 먼저 하고, OpenAI에는 청크 텍스트를 보내 **벡터만 받아온다**.
  OpenAI는 상태를 저장하지 않는 변환기 역할 — 문서도 벡터도 우리 DB에만 있다.
- 청크 **원문 텍스트를 DB에 함께 저장**하므로, 임베딩 모델 교체 시 PDF 재파싱
  없이 재임베딩만 하면 된다.
- 벡터 DB 없이 numpy 전수 비교 — 수천 청크 규모에선 충분히 빠르고
  SQLite(로컬)/Postgres(VM) 어디서나 동일 동작. 수십만 청크가 되면 pgvector 승격.

## 현재 상태 (2026-07-20)

| 문서 | 쪽수 | 청크 |
|---|---:|---:|
| A10/ACOS_5.1.2-p14_ADC_Guide.pdf | 1,015 | 408 |
| A10/ACOS_5.1.2-p14_GSLB_Guide.pdf | 186 | 77 |
| A10/ACOS_6.0.8_ADC_Guide.pdf | 968 | 397 |
| A10/ACOS_6.0.8_GSLB_Guide.pdf | 215 | 90 |
| Arista/EOS_4.36.1F_User_Guide.pdf | 5,387 | 2,222 |
| **합계** | **7,771** | **3,194** |

- 임베딩 모델: `text-embedding-3-small` (전체 인제스트 비용 ~$0.06)
- 한국어 질문 → 영어 문서 교차 매칭 검증됨
- 기술지원 에이전트가 `search_references` 도구로 사용, 출처는 `(문서명 p.페이지)` 표기

## 폴더 구조 (벤더/유형 2단계)

```
backend/reference_docs/
  A10/
    config/    ACOS_6.0.8_ADC_Guide.pdf ...        # 설정 가이드 (PDF, 페이지 청킹)
    release/   ACOS_6.0.8_Release_Notes.pdf ...    # 릴리즈 노트 (PDF)
    issues/    ACOS_6.0.8_Issues.xlsx ...          # 이슈 목록 (XLSX, 행 단위 청킹)
  Arista/
    config/    EOS_4.36.1F_User_Guide.pdf
```

- **유형 폴더명(config/release/issues)이 그대로 `doc_type` 메타데이터**가 되어
  검색 필터로 쓰인다 (`--type issues`, 에이전트 도구의 `doc_type` 파라미터).
  폴더명은 자유 형식이지만 위 세 가지를 권장.
- **XLSX는 행 단위 청킹**: 첫 행을 헤더로 보고 이후 각 행을 "컬럼명: 값" 텍스트로
  변환 — 이슈 1건 = 청크 1개, 출처는 "시트명 N행"으로 표기된다.
  PDF로 변환하지 말고 엑셀 그대로 넣을 것 (표 구조 보존).

## 구성 요소

| 무엇 | 위치 |
|---|---|
| 원본 문서 | `backend/reference_docs/<벤더>/<유형>/` — **git 제외** (용량·라이선스). 파일명 규칙 `<OS>_<버전>_<문서유형>.{pdf,xlsx}` |
| 모델 | `api/models.py` — `ReferenceDocument`(파일 단위, sha256 해시), `ReferenceChunk`(청크+벡터) |
| 파이프라인/검색 | `api/services/references.py` |
| 에이전트 도구 | `api/services/help_agent.py` — `search_references` (tech 에이전트) |
| 설정 | `.env`의 `CASEFLOW_EMBEDDING_MODEL` (기본 text-embedding-3-small), `OPENAI_API_KEY` 사용 |

## 확인 방법

- **OpenAI 사용량/비용**: https://platform.openai.com/usage — 날짜별 사용량에
  `text-embedding-3-small` 항목으로 집계. 결제 상세는 Settings → Billing.
- **Django admin**: `:8000/admin` → Reference documents(문서·청크 수·모델),
  Reference chunks(청크 본문 검색 가능).
- **검색 테스트** (검증 질문 세트 평가용):

  ```bash
  python manage.py search_references "VRRP preempt 동작 조건" --vendor A10
  python manage.py search_references "MLAG peer failure" --vendor Arista --full
  ```

- **화면**: AI 도우미에 기술 질문 → 문서 인용 답변 확인.

## 운영 절차

**문서 추가/교체**
```bash
# PDF를 backend/reference_docs/<벤더>/에 넣고
python manage.py ingest_references     # sha256 해시로 새/변경 파일만 임베딩
```
폴더에서 지운 파일은 DB에서도 자동 제거된다.

**임베딩 모델 교체** (예: 한국어 매칭 미흡 시 large로 승격)
```bash
# .env: CASEFLOW_EMBEDDING_MODEL=text-embedding-3-large
python manage.py ingest_references --force   # 전체 재임베딩, ~$0.4, 몇 분
```
서로 다른 모델의 벡터는 호환되지 않으므로 교체 시 반드시 `--force` 전체 재임베딩.
검색은 현재 설정 모델의 벡터만 사용하므로 교체 중에도 안전하다.

**VM 배포 시**
git에 PDF가 없으므로 코드 배포 외에 별도 이관 필요:
```bash
scp -r backend/reference_docs case@192.168.74.158:~/IDE_Test/google_antigravity/case-flow/backend/
# VM에서
docker compose exec backend python manage.py ingest_references
```
(VM에서 재실행해도 임베딩 비용이 다시 드는 것뿐 — ~$0.06 — 문제 없음.
DB 덤프로 청크를 옮겼다면 해시가 같아 전부 건너뛴다.)

## 비용 요약

| 작업 | 비용 |
|---|---|
| 전체 인제스트 (small) | ~$0.06 |
| 전체 재임베딩 (large 승격) | ~$0.40 |
| 질의 1건 임베딩 | 사실상 0 (수십 토큰) |
| 벡터 저장 | DB ~19MB |
