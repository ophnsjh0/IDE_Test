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
    """로그인 화면의 계정 발급 요청. 승인자가 메일의 승인 링크를 누르면 계정이 생성된다.

    비밀번호는 요청 시점에 해시로만 저장되고(평문 미보관), 승인 시 그 해시가
    그대로 User.password가 된다.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
    ]

    username = models.CharField(max_length=150)
    name = models.CharField(max_length=100, blank=True, default='')
    reason = models.CharField(max_length=300, blank=True, default='')
    password_hash = models.CharField(max_length=128)
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
    출처 케이스가 삭제돼도 지식은 남도록 SET_NULL.
    """
    STATUS_CHOICES = [
        ('draft', 'AI Draft'),
        ('confirmed', 'Confirmed'),
    ]

    case = models.ForeignKey(Case, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name='knowledge_items')
    vendor = models.CharField(max_length=50, choices=Case.VENDOR_CHOICES)
    title = models.CharField(max_length=200)          # 문제 한 줄 요약 (목록 표시용)
    problem = models.TextField()                      # 증상/문제 상황
    root_cause = models.TextField(blank=True, default='')
    resolution = models.TextField()                   # 해결 조치 (CLI 커맨드 포함)
    device_model = models.CharField(max_length=100, blank=True, default='')
    software_version = models.CharField(max_length=50, blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')
    analyzed_by = models.CharField(max_length=100, blank=True, default='')  # 추출 모델 id
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.knowledge_id} [{self.vendor}] {self.title}"

    @property
    def knowledge_id(self):
        return f"K-{100 + self.id}"


class ReferenceDocument(models.Model):
    """벤더 공식 문서(config guide 등) 원본 1개. reference_docs/<벤더>/ 파일과 1:1.

    파일 sha256으로 변경 감지 — 같은 해시면 인제스트를 건너뛰고,
    바뀌면 청크를 지우고 다시 만든다 (ingest_references 커맨드).
    """
    vendor = models.CharField(max_length=50, choices=Case.VENDOR_CHOICES)
    filename = models.CharField(max_length=255, unique=True)  # "A10/ACOS_6.0.8_ADC_Guide.pdf"
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
