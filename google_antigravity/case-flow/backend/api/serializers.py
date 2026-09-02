from django.utils import timezone
from rest_framework import serializers

from .models import (Case, CaseEmail, ChatSession, ChatTurn, KnowledgeItem,
                     Lab, LabLink, LabNetwork, LabNode, LabNodeAccess)


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
    source_session = serializers.SerializerMethodField()
    source_run = serializers.SerializerMethodField()
    author = serializers.SerializerMethodField()

    def get_source_case(self, obj):
        if obj.case is None:
            return None
        return {'id': obj.case.id, 'case_id': obj.case.case_id,
                'status': obj.case.status,
                'vendor_case_number': obj.case.vendor_case_number}

    def get_source_session(self, obj):
        # 대화 원문은 본인만 볼 수 있으므로 세션 내용이 아닌 존재 표시만 노출
        if obj.chat_session is None:
            return None
        return {'id': obj.chat_session.id, 'title': obj.chat_session.title}

    def get_author(self, obj):
        # 직접 작성 항목에서 "누가 썼나"는 근거의 일부다
        return obj.created_by.username if obj.created_by else None

    def get_source_run(self, obj):
        if obj.lab_run is None:
            return None
        run = obj.lab_run
        return {'id': run.id, 'lab': run.lab.name, 'blueprint': run.blueprint.name,
                'status': run.status}

    class Meta:
        model = KnowledgeItem
        fields = ['id', 'knowledge_id', 'vendor', 'title', 'environment', 'problem',
                  'diagnosis', 'root_cause', 'resolution', 'verification', 'caveats',
                  'related_refs', 'device_model', 'software_version', 'status',
                  'analyzed_by', 'references', 'source', 'source_case',
                  'source_session', 'source_run', 'author',
                  'created_at', 'updated_at']
        # source는 출처 서열이라 사람이 고쳐 쓰면 안 된다 — 만든 경로가 정한다
        read_only_fields = ['vendor', 'analyzed_by', 'references', 'source']


class KnowledgeCreateSerializer(serializers.ModelSerializer):
    """직접 작성 전용.

    본 시리얼라이저와 갈라둔 이유는 vendor 때문이다. 추출된 지식은 벤더가
    출처에서 정해지므로 읽기 전용이지만, 직접 작성은 사람이 골라야 한다.
    source·created_by는 뷰가 채운다 — 사람이 출처를 '벤더 케이스'라고
    적어 넣을 수 있으면 신뢰도 서열이 무너진다.
    """

    class Meta:
        model = KnowledgeItem
        fields = ['id', 'vendor', 'title', 'environment', 'problem', 'diagnosis',
                  'root_cause', 'resolution', 'verification', 'caveats',
                  'related_refs', 'device_model', 'software_version']

    def validate_title(self, value):
        if not value.strip():
            raise serializers.ValidationError('제목이 필요합니다.')
        return value.strip()


class ChatTurnSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatTurn
        fields = ['id', 'role', 'content', 'agent', 'model', 'tool_calls',
                  'files', 'attachments', 'created_at']


class ChatSessionSerializer(serializers.ModelSerializer):
    turn_count = serializers.IntegerField(source='turns.count', read_only=True)

    class Meta:
        model = ChatSession
        fields = ['id', 'title', 'turn_count', 'created_at', 'updated_at']


class ChatSessionDetailSerializer(ChatSessionSerializer):
    turns = ChatTurnSerializer(many=True, read_only=True)

    class Meta(ChatSessionSerializer.Meta):
        fields = ChatSessionSerializer.Meta.fields + ['turns']


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


class LabNodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LabNode
        fields = ['name', 'display_name', 'eve_id', 'template', 'image', 'icon',
                  'left', 'top',
                  'ram', 'cpu', 'ethernet', 'console_url', 'running']


class LabNetworkSerializer(serializers.ModelSerializer):
    class Meta:
        model = LabNetwork
        fields = ['name', 'display_name', 'eve_id', 'net_type', 'left', 'top']


class LabLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = LabLink
        fields = ['source', 'source_port', 'source_is_network',
                  'target', 'target_port', 'target_is_network']


class LabSerializer(serializers.ModelSerializer):
    """목록용 — 토폴로지는 싣지 않는다(랩이 늘어나면 목록이 무거워진다)."""
    node_count = serializers.SerializerMethodField()
    server = serializers.CharField(source='server.base_url', read_only=True)

    def get_node_count(self, obj):
        return obj.nodes.count()

    class Meta:
        model = Lab
        fields = ['id', 'path', 'name', 'vendor', 'description', 'server',
                  'node_count', 'topology_synced_at']


class LabDetailSerializer(LabSerializer):
    nodes = LabNodeSerializer(many=True, read_only=True)
    networks = LabNetworkSerializer(many=True, read_only=True)
    links = LabLinkSerializer(many=True, read_only=True)

    class Meta(LabSerializer.Meta):
        fields = LabSerializer.Meta.fields + ['nodes', 'networks', 'links']


class LabNodeAccessSerializer(serializers.ModelSerializer):
    """비밀번호는 받기만 하고 내보내지 않는다. 저장돼 있는지 여부만 알려준다."""
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    has_password = serializers.SerializerMethodField()

    def get_has_password(self, obj):
        return bool(obj.password)

    class Meta:
        model = LabNodeAccess
        fields = ['node_name', 'role', 'mgmt_ip', 'driver', 'username',
                  'password', 'has_password']
