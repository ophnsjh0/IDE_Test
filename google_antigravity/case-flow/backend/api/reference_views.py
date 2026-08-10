"""벤더 문서(Documents) API — 목록/열람·다운로드/업로드/임베딩 관리.

원본 파일은 reference_docs/<벤더>/[<유형>/] 폴더가 단일 진실 소스이고,
ReferenceDocument는 임베딩 산출물이다. 목록은 파일시스템 스캔 + DB 병합으로
만들어 아직 임베딩되지 않은 파일도 함께 보인다.

권한: 열람·다운로드는 전 역할(문서는 전사 공개), 업로드는 엔지니어 이상,
임베딩 실행·자동 임베딩 스위치·삭제는 관리자만.
"""
import logging
import re
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.db.models import Count
from django.http import FileResponse
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Case, ReferenceDocument
from .permissions import IsAdminRole, IsEngineerOrAbove
from .services import references
from .services.usage import log_event

logger = logging.getLogger(__name__)

ALLOWED_SUFFIXES = ('.pdf', '.xlsx')
CONTENT_TYPES = {
    '.pdf': 'application/pdf',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
}
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
# 하위 폴더명(문서 유형): 경로 구분자·숨김 파일 등 경로 조작 여지를 차단
RE_DOC_TYPE = re.compile(r'^[A-Za-z0-9가-힣 _.\-]{1,30}$')


def _resolve_path(raw):
    """클라이언트가 준 상대경로를 reference_docs 안의 실제 파일로 안전 변환.

    디렉터리 탈출(.. 등)·허용 외 확장자·없는 파일이면 None.
    """
    root = settings.REFERENCE_DOCS_DIR.resolve()
    raw = (raw or '').strip()
    if not raw or raw.startswith(('/', '\\')):
        return None
    candidate = (root / raw).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    if candidate.suffix.lower() not in ALLOWED_SUFFIXES:
        return None
    return candidate


class ReferenceListView(APIView):
    """GET /api/references/ — 문서 목록 (전 역할).

    파일시스템 스캔 결과에 임베딩 상태(DB)를 병합한다.
    embedded는 현재 임베딩 모델로 청크가 만들어져 있는지 여부 —
    모델을 교체하면 전부 미임베딩으로 표시되어 재처리 대상이 드러난다.
    """

    def get(self, request):
        # 저장된 chunk_count 필드가 아니라 실제 청크 행 수로 판정한다 —
        # 필드만 믿으면 인제스트가 중간에 깨졌을 때 청크 0개인 문서가
        # "임베딩됨"으로 보인다 (2026-08-10 실장애).
        docs = {d.filename: d for d in
                ReferenceDocument.objects.annotate(n_chunks=Count('chunks'))}
        model = settings.EMBEDDING_MODEL
        items = []
        for vendor, doc_type, relative, path in references.scan_files():
            doc = docs.get(relative)
            stat = path.stat()
            embedded = bool(doc and doc.n_chunks and doc.embedding_model == model)
            items.append({
                'filename': relative,
                'name': path.name,
                'vendor': vendor,
                'doc_type': doc_type,
                'size': stat.st_size,
                'modified_at': datetime.fromtimestamp(
                    stat.st_mtime, tz=dt_timezone.utc).isoformat(),
                'title': doc.title if doc else '',
                'page_count': doc.page_count if doc else 0,
                'chunk_count': doc.n_chunks if doc else 0,
                'embedded': embedded,
                'embedded_at': doc.updated_at.isoformat() if embedded else None,
            })
        return Response({
            'items': items,
            'pending': sum(1 for i in items if not i['embedded']),
            'auto_embed': references.is_auto_embed_enabled(),
            'embedding_model': model,
            'embedding_key_configured': bool(settings.OPENAI_API_KEY),
        })


class ReferenceFileView(APIView):
    """GET /api/references/file/?path=... — 원본 열람/다운로드 (전 역할).

    기본은 inline(브라우저 PDF 뷰어 — #page=N 프래그먼트로 출처 페이지 이동),
    ?dl=1이면 attachment 다운로드. DELETE는 관리자 전용으로 파일과
    임베딩 데이터를 함께 제거한다.
    """

    def get_permissions(self):
        if self.request.method == 'DELETE':
            return [IsAdminRole()]
        return super().get_permissions()

    def get(self, request):
        raw = request.query_params.get('path', '')
        path = _resolve_path(raw)
        if path is None:
            return Response({'error': '문서를 찾을 수 없습니다.'},
                            status=status.HTTP_404_NOT_FOUND)
        as_download = request.query_params.get('dl') == '1'
        response = FileResponse(open(path, 'rb'),
                                content_type=CONTENT_TYPES[path.suffix.lower()])
        disposition = 'attachment' if as_download else 'inline'
        # 파일명에 한글 등 비ASCII가 올 수 있어 RFC 5987 형식으로 지정
        response['Content-Disposition'] = (
            f"{disposition}; filename*=UTF-8''{quote(path.name)}")
        log_event(request.user, 'doc_download',
                  detail=f"{'dl' if as_download else 'view'} {raw}"[:300])
        return response

    def delete(self, request):
        raw = request.query_params.get('path', '')
        path = _resolve_path(raw)
        if path is None:
            return Response({'error': '문서를 찾을 수 없습니다.'},
                            status=status.HTTP_404_NOT_FOUND)
        relative = str(path.relative_to(settings.REFERENCE_DOCS_DIR.resolve()))
        path.unlink()
        references.delete_document(relative)
        log_event(request.user, 'doc_delete', detail=relative)
        return Response({'message': f'{relative} 문서를 삭제했습니다.'})


class ReferenceUploadView(APIView):
    """POST /api/references/upload/ — 문서 업로드 (엔지니어 이상).

    multipart: file, vendor, doc_type(선택), overwrite(선택 'true').
    자동 임베딩 스위치가 켜져 있으면 저장 직후 그 파일만 임베딩한다
    (실패해도 파일은 남고, 관리자가 수동 임베딩으로 재시도).
    """

    permission_classes = [IsEngineerOrAbove]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get('file')
        vendor = (request.data.get('vendor') or '').strip()
        doc_type = (request.data.get('doc_type') or '').strip()
        overwrite = request.data.get('overwrite') == 'true'

        error = self._validate(upload, vendor, doc_type)
        if error:
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

        name = Path(upload.name).name
        target_dir = settings.REFERENCE_DOCS_DIR / vendor
        if doc_type:
            target_dir = target_dir / doc_type
        target = target_dir / name
        if target.exists() and not overwrite:
            return Response(
                {'error': f'같은 이름의 문서가 이미 있습니다: {name}. '
                          '덮어쓰려면 다시 업로드에서 덮어쓰기를 선택하세요.',
                 'exists': True},
                status=status.HTTP_409_CONFLICT)

        target_dir.mkdir(parents=True, exist_ok=True)
        with open(target, 'wb') as f:
            for chunk in upload.chunks():
                f.write(chunk)
        relative = str(target.relative_to(settings.REFERENCE_DOCS_DIR))
        log_event(request.user, 'doc_upload', detail=relative)

        result = {'filename': relative, 'embedded': False}
        if references.is_auto_embed_enabled():
            try:
                with references.embed_lock():
                    references.ingest_file(vendor, doc_type, relative, target)
                result['embedded'] = True
            except (references.EmbeddingUnavailable,
                    references.EmbedInProgress) as e:
                result['embed_error'] = str(e)
            except Exception:
                logger.exception('auto embed failed: %s', relative)
                result['embed_error'] = ('임베딩에 실패했습니다. 파일은 저장되었으니 '
                                         '관리자가 수동 임베딩으로 재시도할 수 있습니다.')
        return Response(result, status=status.HTTP_201_CREATED)

    @staticmethod
    def _validate(upload, vendor, doc_type):
        if upload is None:
            return '파일이 필요합니다.'
        name = Path(upload.name).name
        if not name or name.startswith(('.', '~$')):
            return '허용되지 않는 파일명입니다.'
        if Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
            return 'PDF 또는 XLSX 파일만 업로드할 수 있습니다.'
        if upload.size > MAX_UPLOAD_BYTES:
            return f'파일이 너무 큽니다 (최대 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB).'
        if vendor not in {v for v, _ in Case.VENDOR_CHOICES}:
            return '벤더를 선택하세요.'
        if doc_type and not RE_DOC_TYPE.match(doc_type):
            return '문서 유형은 30자 이내의 문자/숫자만 가능합니다.'
        if doc_type.startswith('.') or '..' in doc_type:
            return '허용되지 않는 문서 유형입니다.'
        return None


class ReferenceEmbedView(APIView):
    """POST /api/references/embed/ — 수동 임베딩 실행 (관리자 전용).

    본문에 path가 있으면 그 파일만 강제 재임베딩, 없으면 전체 스캔
    (신규/변경 파일만 처리, 폴더에서 사라진 문서는 DB에서도 정리 —
    ingest_references 커맨드와 같은 동작).
    """

    permission_classes = [IsAdminRole]

    def post(self, request):
        raw = (request.data.get('path') or '').strip()
        try:
            with references.embed_lock():
                summary = (self._embed_one(raw) if raw else self._embed_all())
        except references.EmbedInProgress as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        except references.EmbeddingUnavailable as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            # 여기서 잡지 않으면 Django가 HTML 500 페이지를 돌려주고, 프론트의
            # JSON 파싱이 깨져 원인과 무관한 "서버 연결 실패"로 표시된다.
            logger.exception('embed failed (%s)', raw or 'all')
            return Response(
                {'error': '임베딩 중 오류가 발생했습니다. 서버 로그를 확인하세요.'},
                status=status.HTTP_502_BAD_GATEWAY)
        if 'error' in summary:
            return Response(summary, status=status.HTTP_404_NOT_FOUND)
        log_event(request.user, 'doc_embed',
                  detail=(raw or f"all created={summary.get('created')} "
                                 f"updated={summary.get('updated')}"))
        return Response(summary)

    @staticmethod
    def _embed_one(raw):
        for vendor, doc_type, relative, path in references.scan_files():
            if relative == raw:
                outcome = references.ingest_file(vendor, doc_type, relative, path,
                                                 force=True)
                return {'processed': 1, 'outcome': outcome, 'filename': relative}
        return {'error': '문서를 찾을 수 없습니다.'}

    @staticmethod
    def _embed_all():
        counts = {'created': 0, 'updated': 0, 'skipped': 0, 'failed': 0}
        seen = set()
        for vendor, doc_type, relative, path in references.scan_files():
            seen.add(relative)
            try:
                counts[references.ingest_file(vendor, doc_type, relative, path)] += 1
            except references.EmbeddingUnavailable:
                raise
            except Exception:
                counts['failed'] += 1
                logger.exception('embed failed: %s', relative)
        removed = ReferenceDocument.objects.exclude(filename__in=seen)
        counts['removed'] = removed.count()
        if counts['removed']:
            removed.delete()
        return counts


class ReferenceAutoEmbedView(APIView):
    """GET/PUT /api/settings/reference-auto-embed/ — 업로드 자동 임베딩 스위치.

    조회는 전 역할(상태 표시), 변경은 관리자만 (임베딩은 API 비용 발생).
    """

    def get_permissions(self):
        if self.request.method == 'PUT':
            return [IsAdminRole()]
        return super().get_permissions()

    def get(self, request):
        return Response({'enabled': references.is_auto_embed_enabled()})

    def put(self, request):
        enabled = request.data.get('enabled')
        if not isinstance(enabled, bool):
            return Response({'error': 'enabled는 true/false여야 합니다.'},
                            status=status.HTTP_400_BAD_REQUEST)
        references.set_auto_embed_enabled(enabled)
        # 이벤트명은 UsageEvent.event(max 20자) 제한에 맞춘다
        log_event(request.user, 'doc_auto_toggle',
                  detail='on' if enabled else 'off')
        return Response({'enabled': references.is_auto_embed_enabled()})
