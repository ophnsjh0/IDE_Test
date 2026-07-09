from django.db import models


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
