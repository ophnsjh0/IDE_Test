from django.db import models

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

    vendor = models.CharField(max_length=50, choices=VENDOR_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Open')
    summary = models.CharField(max_length=200)
    description = models.TextField(blank=True, null=True)
    action_steps = models.TextField(blank=True, null=True)
    resolution = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.vendor} - {self.summary}"

    @property
    def case_id(self):
        return f"C-{1000 + self.id}"
