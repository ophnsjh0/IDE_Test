"""벤더 공식 문서(PDF) 인제스트 + 임베딩 벡터 검색.

파이프라인: reference_docs/<벤더>/*.pdf → 페이지 텍스트 추출(pypdf)
→ 청킹(~4,000자, 페이지 경계 추적, 오버랩) → OpenAI 임베딩(배치)
→ ReferenceChunk에 float32 bytes로 저장.

검색: 질문을 같은 모델로 임베딩 → numpy 코사인 유사도 전수 비교.
청크 수천 개 규모라 벡터 DB 없이 충분히 빠르며(수십 ms), SQLite/Postgres
어느 쪽에서도 동일하게 동작한다. 문서가 수십만 청크로 커지면 pgvector 승격.
"""
import hashlib
import logging

import numpy as np
from django.conf import settings

from api.models import Case, ReferenceChunk, ReferenceDocument

logger = logging.getLogger(__name__)

# 청크 크기(문자). 영문 기준 ~1,000토큰. 오버랩은 섹션 경계에서 문맥이
# 끊기지 않게 하는 안전장치.
CHUNK_CHARS = 4000
OVERLAP_CHARS = 600
# OpenAI 임베딩 API 1회 호출당 청크 수 (요청당 토큰 상한 대비 보수적으로)
EMBED_BATCH = 64


class EmbeddingUnavailable(Exception):
    pass


def _client():
    if not settings.OPENAI_API_KEY:
        raise EmbeddingUnavailable('OPENAI_API_KEY가 설정되어 있지 않습니다.')
    from openai import OpenAI
    return OpenAI(api_key=settings.OPENAI_API_KEY)


def embed_texts(texts, model=None):
    """텍스트 목록을 임베딩해 float32 ndarray (n, dim)로 반환."""
    model = model or settings.EMBEDDING_MODEL
    client = _client()
    vectors = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = [t[:24000] for t in texts[i:i + EMBED_BATCH]]  # 모델 입력 상한 방어
        response = client.embeddings.create(model=model, input=batch)
        vectors.extend(item.embedding for item in response.data)
    return np.asarray(vectors, dtype=np.float32)


# ---------------------------------------------------------------- 인제스트

def scan_files():
    """reference_docs/<벤더>/*.pdf 목록을 (벤더, 상대경로, 절대경로)로 반환."""
    root = settings.REFERENCE_DOCS_DIR
    vendors = {v for v, _ in Case.VENDOR_CHOICES}
    files = []
    if not root.exists():
        return files
    for pdf in sorted(root.rglob('*.pdf')):
        vendor = pdf.parent.name
        if vendor not in vendors:
            logger.warning('reference_docs: 알 수 없는 벤더 폴더 무시: %s', pdf)
            continue
        files.append((vendor, str(pdf.relative_to(root)), pdf))
    return files


def extract_pages(path):
    """PDF에서 페이지별 텍스트를 추출해 [(페이지번호, 텍스트)]로 반환."""
    from pypdf import PdfReader
    reader = PdfReader(path)
    pages = []
    for number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or '').strip()
        if text:
            pages.append((number, text))
    return pages


def extract_title(pages):
    """첫 텍스트 페이지의 앞부분을 문서 제목으로 사용 (예: 'ACOS 6.0.8 ADC Guide')."""
    if not pages:
        return ''
    first = ' '.join(pages[0][1].split())
    # 저작권 문구 전까지가 보통 표지 제목
    for marker in ('©', 'Copyright', 'All rights reserved'):
        cut = first.find(marker)
        if cut > 0:
            first = first[:cut]
    return first.strip()[:300]


def chunk_pages(pages):
    """페이지 텍스트를 이어붙여 CHUNK_CHARS 단위로 자른다.

    반환: [{'page_start', 'page_end', 'text'}]. 청크 머리에 페이지 범위를
    붙이지 않고 필드로만 유지 — 본문은 순수 문서 텍스트.
    """
    chunks = []
    buffer = ''
    start_page = None
    last_page = None

    def flush(end_page):
        if buffer.strip():
            chunks.append({'page_start': start_page, 'page_end': end_page,
                           'text': buffer.strip()})

    for number, text in pages:
        if start_page is None:
            start_page = number
        buffer += ('\n' if buffer else '') + text
        last_page = number
        while len(buffer) >= CHUNK_CHARS:
            head, rest = buffer[:CHUNK_CHARS], buffer[CHUNK_CHARS - OVERLAP_CHARS:]
            chunks.append({'page_start': start_page, 'page_end': number,
                           'text': head.strip()})
            buffer = rest
            start_page = number  # 오버랩 이후는 현재 페이지부터로 근사
    flush(last_page)
    return chunks


def ingest_file(vendor, relative_path, path, force=False, log=lambda msg: None):
    """PDF 1개를 인제스트. 해시가 같으면 건너뜀 (force=True로 강제 재처리).

    반환: 'skipped' | 'created' | 'updated'
    """
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    model = settings.EMBEDDING_MODEL
    doc = ReferenceDocument.objects.filter(filename=relative_path).first()
    if doc and doc.sha256 == sha256 and doc.embedding_model == model and not force:
        return 'skipped'

    log('  텍스트 추출 중...')
    pages = extract_pages(path)
    chunks = chunk_pages(pages)
    log(f'  {len(pages)}쪽 → 청크 {len(chunks)}개, 임베딩 호출 중...')
    vectors = embed_texts([c['text'] for c in chunks], model=model)

    outcome = 'updated' if doc else 'created'
    if doc is None:
        doc = ReferenceDocument(filename=relative_path)
    doc.vendor = vendor
    doc.title = extract_title(pages)
    doc.sha256 = sha256
    doc.page_count = pages[-1][0] if pages else 0
    doc.chunk_count = len(chunks)
    doc.embedding_model = model
    doc.save()

    doc.chunks.all().delete()
    ReferenceChunk.objects.bulk_create([
        ReferenceChunk(document=doc, seq=i, page_start=c['page_start'],
                       page_end=c['page_end'], text=c['text'],
                       embedding=vectors[i].tobytes(), embedding_model=model)
        for i, c in enumerate(chunks)
    ], batch_size=200)
    _invalidate_cache()
    return outcome


# ---------------------------------------------------------------- 검색

# 프로세스 내 벡터 캐시: (모델, 청크 수, 최신 문서 updated_at)이 같으면 재사용
_cache = {'key': None, 'matrix': None, 'chunk_ids': None}


def _invalidate_cache():
    _cache['key'] = None


def _load_matrix(model):
    from django.db.models import Max
    stats = ReferenceChunk.objects.filter(embedding_model=model).aggregate(
        n=Max('id'), latest=Max('document__updated_at'))
    key = (model, stats['n'], stats['latest'])
    if _cache['key'] == key and _cache['matrix'] is not None:
        return _cache['matrix'], _cache['chunk_ids']

    rows = list(ReferenceChunk.objects.filter(embedding_model=model)
                .values_list('id', 'embedding'))
    if not rows:
        return None, None
    matrix = np.vstack([np.frombuffer(e, dtype=np.float32) for _, e in rows])
    # 코사인용 정규화 (OpenAI 임베딩은 이미 단위 벡터지만 방어적으로)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    _cache.update(key=key, matrix=matrix, chunk_ids=[i for i, _ in rows])
    return matrix, _cache['chunk_ids']


def search(query, vendor='', top_k=5):
    """질문과 가장 유사한 문서 청크를 반환.

    반환: [{'document', 'title', 'vendor', 'pages', 'score', 'text'}]
    임베딩된 문서가 없으면 빈 목록.
    """
    model = settings.EMBEDDING_MODEL
    matrix, chunk_ids = _load_matrix(model)
    if matrix is None:
        return []

    vector = embed_texts([query], model=model)[0]
    vector /= np.linalg.norm(vector)
    scores = matrix @ vector

    # 벤더 필터는 상위 후보를 넉넉히 뽑은 뒤 적용 (행렬은 전 벤더 공용)
    order = np.argsort(-scores)[:top_k * 8]
    picked = (ReferenceChunk.objects.filter(id__in=[chunk_ids[i] for i in order])
              .select_related('document'))
    by_id = {c.id: c for c in picked}

    results = []
    for i in order:
        chunk = by_id.get(chunk_ids[i])
        if chunk is None:
            continue
        if vendor and chunk.document.vendor != vendor:
            continue
        results.append({
            'document': chunk.document.filename,
            'title': chunk.document.title,
            'vendor': chunk.document.vendor,
            'pages': f'p.{chunk.page_start}-{chunk.page_end}',
            'score': round(float(scores[i]), 4),
            'text': chunk.text,
        })
        if len(results) >= top_k:
            break
    return results
