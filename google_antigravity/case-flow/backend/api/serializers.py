from django.utils import timezone
from rest_framework import serializers

from .models import Case, CaseEmail, KnowledgeItem


class CaseEmailSerializer(serializers.ModelSerializer):
    class Meta:
        model = CaseEmail
        fields = ['id', 'direction', 'sender', 'recipient', 'subject', 'subject_ko',
                  'body_original', 'body_ko', 'received_at']


class CaseSerializer(serializers.ModelSerializer):
    case_id = serializers.ReadOnlyField()
    date = serializers.SerializerMethodField()

    def get_date(self, obj):
        # Latest mail activity (annotated by the view); manual cases and
        # fresh instances fall back to the row creation time.
        latest = getattr(obj, 'last_email_at', None) or obj.created_at
        return timezone.localtime(latest).strftime('%Y-%m-%d %H:%M:%S')

    class Meta:
        model = Case
        fields = ['id', 'case_id', 'vendor', 'status', 'summary', 'description',
                  'action_steps', 'resolution', 'source', 'vendor_case_number',
                  'device_model', 'device_serial', 'software_version',
                  'analyzed_by', 'date', 'created_at']
        read_only_fields = ['analyzed_by']


class KnowledgeItemSerializer(serializers.ModelSerializer):
    knowledge_id = serializers.ReadOnlyField()
    source_case = serializers.SerializerMethodField()

    def get_source_case(self, obj):
        if obj.case is None:
            return None
        return {'id': obj.case.id, 'case_id': obj.case.case_id,
                'status': obj.case.status,
                'vendor_case_number': obj.case.vendor_case_number}

    class Meta:
        model = KnowledgeItem
        fields = ['id', 'knowledge_id', 'vendor', 'title', 'problem', 'root_cause',
                  'resolution', 'device_model', 'software_version', 'status',
                  'analyzed_by', 'references', 'source_case', 'created_at', 'updated_at']
        read_only_fields = ['vendor', 'analyzed_by', 'references']


class CaseDetailSerializer(CaseSerializer):
    emails = CaseEmailSerializer(many=True, read_only=True)
    related_cases = serializers.SerializerMethodField()

    def get_related_cases(self, obj):
        return [
            {'id': c.id, 'case_id': c.case_id, 'vendor': c.vendor,
             'status': c.status, 'summary': c.summary,
             'vendor_case_number': c.vendor_case_number}
            for c in obj.related_cases.all()
        ]

    class Meta(CaseSerializer.Meta):
        fields = CaseSerializer.Meta.fields + ['emails', 'related_cases']
