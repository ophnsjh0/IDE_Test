from rest_framework import serializers
from .models import Case

class CaseSerializer(serializers.ModelSerializer):
    case_id = serializers.ReadOnlyField()
    date = serializers.DateTimeField(source='created_at', read_only=True, format="%Y-%m-%d")

    class Meta:
        model = Case
        fields = ['id', 'case_id', 'vendor', 'status', 'summary', 'description', 'action_steps', 'resolution', 'date', 'created_at']
