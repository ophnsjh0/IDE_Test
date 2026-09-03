"""지식 항목의 구성도·캡처 이미지 API — 업로드/열람/설명 수정/삭제.

원본 파일은 KNOWLEDGE_IMAGES_DIR/<지식 id>/<uuid>.<확장자>에 있고,
KnowledgeItem.images가 그 메타(설명·어느 칸에 붙는지)를 순서대로 들고 있다.
본문 8칸과 분리한 이유는 models.KnowledgeItem.images 주석에 있다.

권한: 열람은 전 역할(지식은 전사 공개), 업로드·수정·삭제는 엔지니어 이상.
지식 본문을 고칠 수 있는 사람과 같은 경계다.

파일명은 uuid로 새로 짓는다. 사람이 올린 이름을 그대로 쓰면 한글·공백·중복이
경로에 그대로 들어오고, 같은 이름을 두 번 올리면 앞의 그림이 조용히 덮인다.
보여줄 이름은 original_name에 따로 남긴다.
"""
import logging
import uuid
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.http import FileResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import KnowledgeItem
from .permissions import IsEngineerOrAbove
from .services.usage import log_event

logger = logging.getLogger(__name__)

CONTENT_TYPES = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
}
# 확장자는 사람이 바꿔 붙일 수 있으니 실제 내용도 본다. 이 세 형식의 시작 바이트.
MAGIC = (
    (b'\x89PNG\r\n\x1a\n', '.png'),
    (b'\xff\xd8\xff', '.jpg'),
    (b'RIFF', '.webp'),          # RIFF....WEBP — 8번째 바이트부터 다시 확인한다
)
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
# 한 항목에 무한정 쌓이면 목록·상세가 느려지고 디스크도 는다. 구성도 몇 장 +
# 캡처 몇 장이면 충분하다는 판단.
MAX_IMAGES_PER_ITEM = 12
# 이미지를 붙일 수 있는 본문 칸. 프론트 sections.ts와 같은 목록이어야 한다.
SECTION_KEYS = ('environment', 'problem', 'diagnosis', 'root_cause',
                'resolution', 'verification', 'caveats', 'related_refs')


def item_dir(knowledge_id):
    return settings.KNOWLEDGE_IMAGES_DIR / str(knowledge_id)


def resolve_path(raw):
    """클라이언트가 준 상대경로를 이미지 폴더 안의 실제 파일로 안전 변환.

    디렉터리 탈출(.. 등)·허용 외 확장자·없는 파일이면 None.
    """
    root = settings.KNOWLEDGE_IMAGES_DIR.resolve()
    raw = (raw or '').strip()
    if not raw or raw.startswith(('/', '\\')):
        return None
    candidate = (root / raw).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    if candidate.suffix.lower() not in CONTENT_TYPES:
        return None
    return candidate


def sniff_suffix(head):
    """앞머리 바이트로 실제 형식을 판정. 모르는 형식이면 None."""
    for signature, suffix in MAGIC:
        if not head.startswith(signature):
            continue
        # RIFF 컨테이너는 WEBP 말고도 있다 (WAV 등) — 형식 태그까지 봐야 한다
        if suffix == '.webp' and head[8:12] != b'WEBP':
            continue
        return suffix
    return None


def delete_images_dir(knowledge_id):
    """지식 항목이 지워질 때 붙어 있던 그림도 함께 지운다.

    실패해도 예외를 올리지 않는다 — 파일이 남는 것보다 삭제가 막히는 쪽이 나쁘다.
    """
    directory = item_dir(knowledge_id)
    try:
        for child in directory.glob('*'):
            child.unlink(missing_ok=True)
        directory.rmdir()
    except OSError:
        logger.warning('지식 이미지 폴더를 지우지 못했습니다: %s', directory)


class KnowledgeImageListView(APIView):
    """POST/PATCH/DELETE /api/knowledge/<id>/images/ — 첨부 이미지 관리.

    POST   multipart: file, caption(선택), section(선택)
    PATCH  json: {filename, caption, section} — 설명·붙는 칸만 고친다
    DELETE json: {filename}
    셋 다 갱신된 images 목록을 돌려주므로 화면이 다시 조회할 필요가 없다.
    """

    permission_classes = [IsEngineerOrAbove]
    # 업로드는 multipart, 설명 수정·삭제는 JSON으로 온다 — 한쪽만 두면
    # 나머지가 415로 막힌다
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, id):
        item = KnowledgeItem.objects.filter(id=id).first()
        if item is None:
            return Response({'error': '지식 항목을 찾을 수 없습니다.'},
                            status=status.HTTP_404_NOT_FOUND)
        upload = request.FILES.get('file')
        if upload is None:
            return Response({'error': '이미지 파일이 필요합니다.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if upload.size > MAX_UPLOAD_BYTES:
            return Response(
                {'error': f'이미지는 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB까지 올릴 수 있습니다.'},
                status=status.HTTP_400_BAD_REQUEST)
        if len(item.images) >= MAX_IMAGES_PER_ITEM:
            return Response(
                {'error': f'이미지는 항목당 {MAX_IMAGES_PER_ITEM}장까지입니다. '
                          '필요 없는 그림을 지우고 다시 올려주세요.'},
                status=status.HTTP_400_BAD_REQUEST)

        head = upload.read(12)
        upload.seek(0)
        suffix = sniff_suffix(head)
        if suffix is None:
            return Response({'error': 'PNG · JPG · WEBP 이미지만 올릴 수 있습니다.'},
                            status=status.HTTP_400_BAD_REQUEST)

        section = (request.data.get('section') or '').strip()
        if section and section not in SECTION_KEYS:
            return Response({'error': f'알 수 없는 본문 항목입니다: {section}'},
                            status=status.HTTP_400_BAD_REQUEST)

        directory = item_dir(item.id)
        directory.mkdir(parents=True, exist_ok=True)
        name = f'{uuid.uuid4().hex}{suffix}'
        with open(directory / name, 'wb') as f:
            for chunk in upload.chunks():
                f.write(chunk)

        item.images = item.images + [{
            'filename': f'{item.id}/{name}',
            'original_name': Path(upload.name or '').name[:200],
            'caption': (request.data.get('caption') or '').strip()[:300],
            'section': section,
            'uploaded_by': request.user.username,
            'uploaded_at': timezone.now().isoformat(),
            'size_bytes': upload.size,
        }]
        item.save(update_fields=['images', 'updated_at'])
        log_event(request.user, 'knowledge_image_upload',
                  detail=f'{item.knowledge_id} {name}')
        return Response({'images': item.images}, status=status.HTTP_201_CREATED)

    def patch(self, request, id):
        item = KnowledgeItem.objects.filter(id=id).first()
        if item is None:
            return Response({'error': '지식 항목을 찾을 수 없습니다.'},
                            status=status.HTTP_404_NOT_FOUND)
        filename = (request.data.get('filename') or '').strip()
        images = list(item.images)
        target = next((i for i in images if i.get('filename') == filename), None)
        if target is None:
            return Response({'error': '그 이미지를 찾을 수 없습니다.'},
                            status=status.HTTP_404_NOT_FOUND)
        if 'caption' in request.data:
            target['caption'] = (request.data.get('caption') or '').strip()[:300]
        if 'section' in request.data:
            section = (request.data.get('section') or '').strip()
            if section and section not in SECTION_KEYS:
                return Response({'error': f'알 수 없는 본문 항목입니다: {section}'},
                                status=status.HTTP_400_BAD_REQUEST)
            target['section'] = section
        item.images = images
        item.save(update_fields=['images', 'updated_at'])
        return Response({'images': item.images})

    def delete(self, request, id):
        item = KnowledgeItem.objects.filter(id=id).first()
        if item is None:
            return Response({'error': '지식 항목을 찾을 수 없습니다.'},
                            status=status.HTTP_404_NOT_FOUND)
        filename = (request.data.get('filename') or '').strip()
        remaining = [i for i in item.images if i.get('filename') != filename]
        if len(remaining) == len(item.images):
            return Response({'error': '그 이미지를 찾을 수 없습니다.'},
                            status=status.HTTP_404_NOT_FOUND)
        # 메타를 먼저 지운다. 파일 삭제가 실패해도 화면에서는 사라지고,
        # 남은 파일은 고아가 될 뿐 아무것도 깨뜨리지 않는다. 반대 순서면
        # 파일은 없는데 목록에 남아 깨진 그림이 뜬다.
        item.images = remaining
        item.save(update_fields=['images', 'updated_at'])
        path = resolve_path(filename)
        if path is not None:
            try:
                path.unlink()
            except OSError:
                logger.warning('지식 이미지를 지우지 못했습니다: %s', path)
        log_event(request.user, 'knowledge_image_delete',
                  detail=f'{item.knowledge_id} {filename}'[:300])
        return Response({'images': item.images})


class KnowledgeImageFileView(APIView):
    """GET /api/knowledge/images/<path> — 이미지 원본 (전 역할).

    <img>가 직접 부르는 자리라 인증은 세션 쿠키로 걸린다. 브라우저가 내용을
    보고 형식을 다시 추측하지 않도록 nosniff를 붙인다 — 이미지 확장자로
    올라온 HTML이 페이지로 실행되는 것을 막는다.
    """

    def get(self, request, name):
        path = resolve_path(name)
        if path is None:
            return Response({'error': '이미지를 찾을 수 없습니다.'},
                            status=status.HTTP_404_NOT_FOUND)
        response = FileResponse(open(path, 'rb'),
                                content_type=CONTENT_TYPES[path.suffix.lower()])
        response['X-Content-Type-Options'] = 'nosniff'
        response['Content-Disposition'] = (
            f"inline; filename*=UTF-8''{quote(path.name)}")
        return response
