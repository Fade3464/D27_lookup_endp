from django.contrib import admin, messages
from .models import Alert, Batch, Engine, Event, Failure, Number, PartialExport, UserQueue
from .queue import clear_safety


class ReadOnlyAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Batch)
class BatchAdmin(ReadOnlyAdmin):
    list_display = ["id", "original_name", "user", "status", "valid_rows", "good_rows", "bad_rows", "error_rows", "created_at"]
    list_filter = ["status", "pause_reason"]
    search_fields = ["original_name", "user__username"]
    list_select_related = ["user"]


@admin.register(Number)
class NumberAdmin(ReadOnlyAdmin):
    list_display = ["batch_id", "sequence", "ani", "status", "attempts"]
    search_fields = ["ani", "batch__original_name"]
    list_filter = ["status"]
    show_full_result_count = False


@admin.register(Event)
class EventAdmin(ReadOnlyAdmin):
    list_display = ["batch_id", "action", "actor", "created_at"]
    list_filter = ["action"]


@admin.register(Failure)
class FailureAdmin(ReadOnlyAdmin):
    list_display = ["number_id", "attempt", "kind", "http_code", "created_at"]
    show_full_result_count = False


@admin.register(Alert)
class AlertAdmin(ReadOnlyAdmin):
    list_display = ["id", "status", "attempts", "created_at", "sent_at"]


@admin.register(Engine)
class EngineAdmin(ReadOnlyAdmin):
    list_display = ["id", "paused", "reason", "consecutive_errors", "paused_at"]
    actions = ["resume_queues", "resume_and_retry"]

    def has_resume_safety_permission(self, request):
        return request.user.is_staff and request.user.has_perm("batches.resume_safety_stop")

    def has_view_permission(self, request, obj=None):
        return self.has_resume_safety_permission(request) or super().has_view_permission(request, obj)

    def recover(self, request, queryset, retry_failed):
        if not queryset.filter(pk=1).exists():
            return
        try:
            result = clear_safety(request.user, retry_failed=retry_failed)
        except (PermissionError, ValueError) as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
        else:
            self.message_user(request, f'Resumed {result["resumed_files"]} files. '
                f'Queued {result["retried_rows"]} failed lookups for retry.', level=messages.SUCCESS)

    @admin.action(permissions=["resume_safety"], description="Resume safety-stopped queues (keep failed rows for review)")
    def resume_queues(self, request, queryset):
        self.recover(request, queryset, False)

    @admin.action(permissions=["resume_safety"], description="Resume safety-stopped queues and retry failed lookups")
    def resume_and_retry(self, request, queryset):
        self.recover(request, queryset, True)


admin.site.register(UserQueue, ReadOnlyAdmin)


@admin.register(PartialExport)
class PartialExportAdmin(ReadOnlyAdmin):
    list_display = ["id", "batch_id", "status", "resolved_rows", "created_at", "ready_at"]
    list_filter = ["status"]
