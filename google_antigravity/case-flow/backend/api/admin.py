from django.contrib import admin
from .models import Case

@admin.register(Case)
class CaseAdmin(admin.ModelAdmin):
    list_display = ('case_id', 'vendor', 'status', 'summary', 'created_at')
    list_filter = ('vendor', 'status')
    search_fields = ('summary', 'description')
    readonly_fields = ('created_at',)
