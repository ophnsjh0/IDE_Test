from django.contrib import admin
from .models import (Case, ChatSession, ChatTurn, ReferenceChunk,
                     ReferenceDocument, UsageEvent)

@admin.register(Case)
class CaseAdmin(admin.ModelAdmin):
    list_display = ('case_id', 'vendor', 'status', 'summary', 'created_at')
    list_filter = ('vendor', 'status')
    search_fields = ('summary', 'description')
    readonly_fields = ('created_at',)


@admin.register(ReferenceDocument)
class ReferenceDocumentAdmin(admin.ModelAdmin):
    list_display = ('filename', 'vendor', 'doc_type', 'title', 'page_count',
                    'chunk_count', 'embedding_model', 'updated_at')
    list_filter = ('vendor', 'doc_type')
    readonly_fields = [f.name for f in ReferenceDocument._meta.fields]


@admin.register(ReferenceChunk)
class ReferenceChunkAdmin(admin.ModelAdmin):
    list_display = ('document', 'seq', 'page_start', 'page_end', 'preview')
    list_filter = ('document',)
    search_fields = ('text',)
    readonly_fields = ('document', 'seq', 'page_start', 'page_end', 'text',
                       'embedding_model')
    exclude = ('embedding',)  # 바이너리 벡터는 admin에 표시 불가

    @admin.display(description='내용 미리보기')
    def preview(self, obj):
        return obj.text[:80]


class ChatTurnInline(admin.TabularInline):
    model = ChatTurn
    readonly_fields = ('role', 'content', 'agent', 'model', 'tool_calls',
                       'files', 'created_at')
    extra = 0
    can_delete = False


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'title', 'updated_at')
    list_filter = ('user',)
    search_fields = ('title', 'turns__content')
    readonly_fields = ('user', 'title', 'created_at', 'updated_at')
    inlines = [ChatTurnInline]


@admin.register(UsageEvent)
class UsageEventAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'user', 'event', 'detail')
    list_filter = ('event', 'user')
    date_hierarchy = 'created_at'
    readonly_fields = ('user', 'event', 'detail', 'created_at')
