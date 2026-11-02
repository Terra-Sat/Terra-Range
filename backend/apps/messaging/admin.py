from django.contrib import admin

from apps.messaging.core.redact import mask_phone
from apps.messaging.models import Consent, ConsentEvent, InboundEvent, OutboundLog, Recipient


class ReadOnlyAdmin(admin.ModelAdmin):
    """Audit views only: nothing here can be added, edited or deleted from the admin."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Recipient)
class RecipientAdmin(ReadOnlyAdmin):
    list_display = ("id", "masked_phone", "ward_code", "language", "source", "created_at")
    list_filter = ("ward_code", "language", "source")
    exclude = ("phone",)

    @admin.display(description="Phone")
    def masked_phone(self, obj):
        return mask_phone(obj.phone)


@admin.register(Consent)
class ConsentAdmin(ReadOnlyAdmin):
    list_display = ("recipient", "channel", "status", "policy_version", "granted_at", "withdrawn_at")
    list_filter = ("channel", "status", "policy_version")


@admin.register(ConsentEvent)
class ConsentEventAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "recipient", "channel", "event_type", "outcome", "from_status", "to_status", "policy_version", "source")
    list_filter = ("channel", "event_type", "outcome", "source")


@admin.register(InboundEvent)
class InboundEventAdmin(ReadOnlyAdmin):
    list_display = ("received_at", "provider", "kind", "intent", "outcome", "processed_at")
    list_filter = ("provider", "kind", "outcome")


@admin.register(OutboundLog)
class OutboundLogAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "provider", "purpose", "message_class", "status", "error_code")
    list_filter = ("provider", "purpose", "message_class", "status")
