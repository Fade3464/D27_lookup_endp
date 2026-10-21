from django.contrib import admin
from .models import Campaign, Lookup

admin.site.site_header = "D27 Operations"
admin.site.site_title = "D27 Admin"
admin.site.index_title = "Users, campaigns & lookup records"


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ["label", "campaign_id", "active"]
    list_filter = ["active"]
    search_fields = ["label", "campaign_id"]
    # IDs define upstream routing; do not silently reinterpret existing records.
    def get_readonly_fields(self, request, obj=None):
        return ["campaign_id"] if obj else []


@admin.register(Lookup)
class LookupAdmin(admin.ModelAdmin):
    list_display = ["id", "ani", "campaign_label", "status", "user", "created_at", "http_code"]
    list_filter = ["status", "campaign", "created_at"]
    search_fields = ["ani", "user__username", "campaign_id_sent"]
    date_hierarchy = "created_at"
    list_select_related = ["user", "campaign"]
    readonly_fields = [field.name for field in Lookup._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
