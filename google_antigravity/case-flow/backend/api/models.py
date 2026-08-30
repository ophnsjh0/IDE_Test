from django.conf import settings as django_settings
from django.db import models


class UserProfile(models.Model):
    """계정별 역할. viewer(조회) < engineer(케이스 조작) < admin(삭제/설정/계정 관리)."""
    ROLE_CHOICES = [
        ('viewer', 'Viewer'),
        ('engineer', 'Engineer'),
        ('admin', 'Admin'),
    ]

    user = models.OneToOneField(django_settings.AUTH_USER_MODEL,
                                on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='viewer')

    def __str__(self):
        return f"{self.user.username} ({self.role})"


class SignupRequest(models.Model):
    """로그인 화면의 계정 발급 신청 기록.

    2026-08-11부터 **신청 즉시 계정이 생성**된다(승인 대기 없음). 관리자에게는
    "누가 가입했다"는 알림 메일만 나가고, 부적절한 가입은 계정 관리에서
    역할 변경·비활성화로 사후 처리한다. 승인 링크 방식은 폐기했다 — 메일
    보안 스캐너가 사람보다 먼저 링크를 눌러 자동 승인되는 문제가 있었다.

    이 모델은 이제 신청 사유·연락처를 남기는 가입 로그다. 비밀번호는 User에만
    저장되고 여기에는 보관하지 않는다(중복 보관 제거).
    requested_role은 신청자가 고른 역할 — admin은 자율 신청 대상에서 제외
    (권한 상승 방지), 반드시 viewer/engineer 중 하나여야 한다.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
    ]
    REQUESTABLE_ROLE_CHOICES = [
        ('viewer', 'Viewer'),
        ('engineer', 'Engineer'),
    ]

    username = models.CharField(max_length=150)
    name = models.CharField(max_length=100, blank=True, default='')
    email = models.EmailField(blank=True, default='')  # 사내 연락처 (공지·문의용)
    reason = models.CharField(max_length=300, blank=True, default='')
    requested_role = models.CharField(max_length=10, choices=REQUESTABLE_ROLE_CHOICES,
                                       default='viewer')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.username} ({self.status})"


class AppSetting(models.Model):
    """런타임에 변경 가능한 앱 설정 key-value 저장소 (예: 프론트에서 선택한 AI 모델)."""
    key = models.CharField(max_length=100, unique=True)
    value = models.CharField(max_length=200, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.key}={self.value}"

    @classmethod
    def get(cls, key, default=''):
        row = cls.objects.filter(key=key).first()
        return row.value if row else default

    @classmethod
    def set(cls, key, value):
        cls.objects.update_or_create(key=key, defaults={'value': value})


class UsageEvent(models.Model):
    """파일럿 기간 사용 측정 이벤트 — 도입 확대 여부를 판단할 지표의 원본.

    기록은 부가 기능이므로 services.usage.log_event()는 어떤 예외도
    호출자에게 전파하지 않는다 (기록 실패가 기능을 깨면 안 됨).
    """
    EVENT_CHOICES = [
        ('login', 'Login'),
        ('case_list', 'Case List View'),
        ('case_view', 'Case Detail View'),
        ('search', 'Search'),
        ('agent_chat', 'AI Agent Chat'),
        ('report_download', 'Report Download'),
        ('gmail_sync', 'Gmail Sync'),
        ('knowledge_view', 'Knowledge View'),
        ('knowledge_extract', 'Knowledge Extract from Chat'),
    ]

    user = models.ForeignKey(django_settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name='usage_events')
    event = models.CharField(max_length=20, choices=EVENT_CHOICES)
    detail = models.CharField(max_length=300, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [models.Index(fields=['event', 'created_at'])]

    def __str__(self):
        return f"{self.user} {self.event} @ {self.created_at:%Y-%m-%d %H:%M}"


class Case(models.Model):
    VENDOR_CHOICES = [
        ('A10', 'A10'),
        ('Arista', 'Arista'),
        ('HPE Aruba', 'HPE Aruba'),
        ('Juniper', 'Juniper'),
    ]
    STATUS_CHOICES = [
        ('Open', 'Open'),
        ('Resolved', 'Resolved'),
        ('Pending', 'Pending'),
    ]

    SOURCE_CHOICES = [
        ('manual', 'Manual'),
        ('email', 'Email'),
    ]

    vendor = models.CharField(max_length=50, choices=VENDOR_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Open')
    summary = models.CharField(max_length=200)
    description = models.TextField(blank=True, null=True)
    action_steps = models.TextField(blank=True, null=True)
    resolution = models.TextField(blank=True, null=True)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='manual')
    # 이 케이스를 마지막으로 분석/번역한 AI 모델 id (예: gemini-3.5-flash)
    analyzed_by = models.CharField(max_length=100, blank=True, default='')
    vendor_case_number = models.CharField(max_length=100, blank=True, null=True, unique=True)
    gmail_thread_id = models.CharField(max_length=100, blank=True, null=True)
    # 메일에서 추출한 장비 정보 (정규식 1차 -> AI 분석 2차, 없으면 빈 값)
    device_model = models.CharField(max_length=100, blank=True, default='')
    device_serial = models.CharField(max_length=200, blank=True, default='')  # 여러 개면 쉼표 병기
    software_version = models.CharField(max_length=50, blank=True, default='')
    # 같은 사건에서 파생됐지만 별도 트랙인 케이스들의 상호 참조 (병합 대신 링크)
    related_cases = models.ManyToManyField('self', blank=True)
    # 지식 추출 검토 완료 시각 — 지식이 없다고 판정된 케이스를 동기화 때마다
    # 다시 AI로 스캔하지 않기 위한 표시 (비용 절감). null = 미검토.
    knowledge_checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.vendor} - {self.summary}"

    @property
    def case_id(self):
        return f"C-{1000 + self.id}"


class KnowledgeItem(models.Model):
    """해결된 케이스에서 추출한 재사용 가능한 기술 지식 (문제-원인-해결).

    AI가 초안(draft)으로 만들고 엔지니어가 확인 후 확정(confirmed)한다.
    출처(케이스 또는 AI 도우미 대화)가 삭제돼도 지식은 남도록 SET_NULL.
    케이스 유래는 벤더가 실제 해결한 기록, 대화 유래는 AI 추론 기반이라
    신뢰도가 한 단계 낮다 — UI에서 출처를 구분해 표시한다.
    """
    STATUS_CHOICES = [
        ('draft', 'AI Draft'),
        ('confirmed', 'Confirmed'),
    ]

    case = models.ForeignKey(Case, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name='knowledge_items')
    chat_session = models.ForeignKey('ChatSession', null=True, blank=True,
                                     on_delete=models.SET_NULL,
                                     related_name='knowledge_items')
    vendor = models.CharField(max_length=50, choices=Case.VENDOR_CHOICES)
    title = models.CharField(max_length=200)          # 문제 한 줄 요약 (목록 표시용)
    # 본문 8칸. 처음엔 케이스 분석 스키마(문제-원인-해결)를 그대로 썼는데, 그쪽은
    # 메일 1건을 "간결히" 줄이는 게 목적이라 재현에 필요한 것들이 resolution 한 칸에
    # 뭉쳤다. 아래 다섯 칸은 그 뭉침을 푼 것 — AI가 못 채우면 빈 값으로 두고
    # 엔지니어가 상세 화면에서 직접 채운다.
    environment = models.TextField(blank=True, default='')   # 전제 조건: 구성·토폴로지·버전
    problem = models.TextField()                      # 증상/문제 상황
    diagnosis = models.TextField(blank=True, default='')     # 진단 절차: 원인을 좁힌 방법
    root_cause = models.TextField(blank=True, default='')
    resolution = models.TextField()                   # 해결 조치 (CLI 커맨드 포함)
    verification = models.TextField(blank=True, default='')  # 조치 후 확인 방법
    caveats = models.TextField(blank=True, default='')       # 주의사항·부작용·적용 조건
    related_refs = models.TextField(blank=True, default='')  # 벤더 버그 ID·케이스 번호 (줄바꿈 구분)
    device_model = models.CharField(max_length=100, blank=True, default='')
    software_version = models.CharField(max_length=50, blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')
    analyzed_by = models.CharField(max_length=100, blank=True, default='')  # 추출 모델 id
    # 이 지식을 뒷받침하는 벤더 공식 문서 발췌 목록 — 벡터 검색 후보를 AI가 선별.
    # [{'document', 'pages', 'score', 'note'}], 관련 문서 없으면 빈 목록.
    references = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.knowledge_id} [{self.vendor}] {self.title}"

    @property
    def knowledge_id(self):
        return f"K-{100 + self.id}"


class ChatSession(models.Model):
    """AI 도우미 대화 세션 — 대화가 휘발되지 않게 서버에 저장한다.

    1차 목적은 데이터 보존(다시 보기·이어가기)이고, 향후 지식 베이스
    2단계(대화에서 지식 추출)의 원천 데이터가 된다. 대화 원문은
    본인만 조회 가능. 계정이 삭제돼도 추출 원천은 남도록 SET_NULL.
    """
    user = models.ForeignKey(django_settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name='chat_sessions')
    title = models.CharField(max_length=200)  # 첫 질문 (목록 표시용)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f"{self.user} - {self.title[:40]}"


class ChatTurn(models.Model):
    """세션 내 대화 턴 1개. assistant 턴에는 담당 에이전트/모델/도구 호출도 남긴다
    (어떤 근거로 답했는지가 지식 추출과 품질 분석의 핵심 재료)."""
    ROLE_CHOICES = [
        ('user', 'User'),
        ('assistant', 'Assistant'),
    ]

    session = models.ForeignKey(ChatSession, related_name='turns',
                                on_delete=models.CASCADE)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField()
    agent = models.CharField(max_length=20, blank=True, default='')
    model = models.CharField(max_length=100, blank=True, default='')
    tool_calls = models.JSONField(default=list, blank=True)  # [{'name', 'input'}]
    files = models.JSONField(default=list, blank=True)  # 리포트 문서 메타
    # 사용자가 붙인 첨부 [{'file_id', 'filename', 'kind', 'size_bytes'}].
    # 원본은 Anthropic Files API에 있고 여기엔 참조만 남는다 — 세션을 지우면
    # 원본도 함께 지운다 (views.ChatSessionDetailView.delete).
    attachments = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"{self.session_id}#{self.id} {self.role}"


class ReferenceDocument(models.Model):
    """벤더 공식 문서(config guide 등) 원본 1개. reference_docs/<벤더>/ 파일과 1:1.

    파일 sha256으로 변경 감지 — 같은 해시면 인제스트를 건너뛰고,
    바뀌면 청크를 지우고 다시 만든다 (ingest_references 커맨드).
    """
    vendor = models.CharField(max_length=50, choices=Case.VENDOR_CHOICES)
    filename = models.CharField(max_length=255, unique=True)  # "A10/config/ACOS_6.0.8_ADC_Guide.pdf"
    # 벤더 하위 폴더명 = 문서 유형 (config/release/issues 권장, 자유 형식).
    # 벤더 폴더 바로 아래 파일은 빈 값.
    doc_type = models.CharField(max_length=30, blank=True, default='')
    title = models.CharField(max_length=300, blank=True, default='')  # 첫 페이지에서 추출
    sha256 = models.CharField(max_length=64)
    page_count = models.IntegerField(default=0)
    chunk_count = models.IntegerField(default=0)
    embedding_model = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"[{self.vendor}] {self.filename} ({self.chunk_count} chunks)"


class ReferenceChunk(models.Model):
    """문서를 검색 단위로 자른 청크 + 임베딩 벡터.

    embedding은 float32 배열의 raw bytes — 모델 교체 시 원문(text)은 그대로 두고
    임베딩만 다시 만든다. 검색은 embedding_model이 현재 설정과 같은 청크만 대상.
    """
    document = models.ForeignKey(ReferenceDocument, related_name='chunks',
                                 on_delete=models.CASCADE)
    seq = models.IntegerField()                     # 문서 내 순번
    page_start = models.IntegerField()
    page_end = models.IntegerField()
    text = models.TextField()
    embedding = models.BinaryField()                # float32[dim] raw bytes
    embedding_model = models.CharField(max_length=100)

    class Meta:
        ordering = ['document_id', 'seq']
        indexes = [models.Index(fields=['embedding_model'])]

    def __str__(self):
        return f"{self.document.filename}#{self.seq} (p.{self.page_start}-{self.page_end})"


class CaseEmail(models.Model):
    DIRECTION_CHOICES = [
        ('inbound', 'Inbound'),   # received from vendor
        ('outbound', 'Outbound'), # sent by us
    ]

    case = models.ForeignKey(Case, related_name='emails', on_delete=models.CASCADE)
    gmail_message_id = models.CharField(max_length=100, unique=True)
    gmail_thread_id = models.CharField(max_length=100, blank=True)
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES, default='inbound')
    sender = models.CharField(max_length=255)
    recipient = models.TextField(blank=True)
    subject = models.CharField(max_length=500)
    subject_ko = models.CharField(max_length=500, blank=True)
    body_original = models.TextField(blank=True)
    body_ko = models.TextField(blank=True)
    received_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['received_at']

    def __str__(self):
        return f"{self.case.case_id} - {self.subject}"


class LabServer(models.Model):
    """EVE-NG 서버 한 대.

    지금은 .env의 서버 하나뿐이고 자격증명도 .env에서 읽는다. 그런데도 랩이
    서버를 참조하게 해두는 이유: 나중에 Pro 서버로 옮길 때 전부 한 번에
    넘기는 컷오버가 아니라, 랩 단위로 옮겨가며 검증할 수 있다.
    """
    base_url = models.CharField(max_length=200, unique=True)
    version = models.CharField(max_length=50, blank=True, default='')  # /api/status
    checked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.base_url} ({self.version or 'unknown'})"


class Lab(models.Model):
    """Case-Flow에 등록된 랩. EVE-NG에 있는 모든 랩이 아니라 관리자가 고른 것만.

    EVE-NG에는 다른 사람 작업용 랩이 섞여 있어서 전부 노출하지 않는다.
    """
    server = models.ForeignKey(LabServer, on_delete=models.CASCADE, related_name='labs')
    # 파일명이 아니라 경로 전체를 쓴다 — Pro는 사용자별 폴더를 쓸 수 있다.
    path = models.CharField(max_length=300)          # '/AI-LAB-A10-OneArm.unl'
    name = models.CharField(max_length=200)          # 화면 표시 이름
    vendor = models.CharField(max_length=50, blank=True, default='')
    description = models.CharField(max_length=300, blank=True, default='')
    topology_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['vendor', 'name']
        unique_together = [('server', 'path')]

    def __str__(self):
        return f"{self.name} ({self.path})"


class LabNode(models.Model):
    """토폴로지 스냅샷의 노드.

    키는 eve_id가 아니라 **이름**이다. eve_id와 console 포트는 랩을 다른
    서버로 옮기면 재부여되므로, 이름을 키로 잡아야 랩 등록 정보(MGMT IP·역할
    매핑 등)가 그대로 붙는다.
    """
    lab = models.ForeignKey(Lab, on_delete=models.CASCADE, related_name='nodes')
    name = models.CharField(max_length=100)
    eve_id = models.IntegerField()                   # 갱신되는 값
    template = models.CharField(max_length=50, blank=True, default='')
    image = models.CharField(max_length=120, blank=True, default='')
    icon = models.CharField(max_length=120, blank=True, default='')
    left = models.IntegerField(default=0)            # EVE-NG 캔버스 좌표 그대로
    top = models.IntegerField(default=0)
    ram = models.IntegerField(default=0)             # MB
    cpu = models.IntegerField(default=0)
    ethernet = models.IntegerField(default=0)
    console_url = models.CharField(max_length=200, blank=True, default='')
    # EVE-NG status != 0. 프로세스가 떠 있다는 뜻이지 부팅 완료가 아니다.
    running = models.BooleanField(default=False)

    class Meta:
        ordering = ['name']
        unique_together = [('lab', 'name')]

    def __str__(self):
        return f"{self.lab.name}/{self.name}"


class LabNetwork(models.Model):
    """토폴로지의 네트워크(브리지·pnet0). 관리망 연결을 표현하는 데 필요하다."""
    lab = models.ForeignKey(Lab, on_delete=models.CASCADE, related_name='networks')
    name = models.CharField(max_length=100)
    eve_id = models.IntegerField()
    net_type = models.CharField(max_length=30, blank=True, default='')  # bridge, pnet0
    left = models.IntegerField(default=0)
    top = models.IntegerField(default=0)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.lab.name}/{self.name} ({self.net_type})"


class LabLink(models.Model):
    """노드 간 배선. 양 끝은 id가 아니라 이름으로 적는다(노드 키와 같은 이유).

    한쪽이 네트워크인 링크(관리망 연결)도 함께 담는다 — 준비 판정이 관리망을
    통해 이뤄지므로 빠뜨리면 나중에 다시 수집해야 한다.
    """
    lab = models.ForeignKey(Lab, on_delete=models.CASCADE, related_name='links')
    source = models.CharField(max_length=100)
    source_port = models.CharField(max_length=50, blank=True, default='')
    source_is_network = models.BooleanField(default=False)
    target = models.CharField(max_length=100)
    target_port = models.CharField(max_length=50, blank=True, default='')
    target_is_network = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.source}:{self.source_port} <-> {self.target}:{self.target_port}"
