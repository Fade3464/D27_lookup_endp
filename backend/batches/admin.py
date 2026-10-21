from django.contrib import admin
from .models import Alert, Batch, Engine, Event, Failure, Number, UserQueue


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


admin.site.register(Engine, ReadOnlyAdmin)
admin.site.register(UserQueue, ReadOnlyAdmin)
