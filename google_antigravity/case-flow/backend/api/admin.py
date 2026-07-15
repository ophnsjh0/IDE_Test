from django.contrib import admin
from .models import Case, UsageEvent

@admin.register(Case)
class CaseAdmin(admin.ModelAdmin):
    list_display = ('case_id', 'vendor', 'status', 'summary', 'created_at')
    list_filter = ('vendor', 'status')
    search_fields = ('summary', 'description')
    readonly_fields = ('created_at',)


@admin.register(UsageEvent)
class UsageEventAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'user', 'event', 'detail')
    list_filter = ('event', 'user')
    date_hierarchy = 'created_at'
    readonly_fields = ('user', 'event', 'detail', 'created_at')
