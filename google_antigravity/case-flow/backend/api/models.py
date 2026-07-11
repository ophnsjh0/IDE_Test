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
