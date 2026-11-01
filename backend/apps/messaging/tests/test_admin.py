from django.contrib import admin

from apps.messaging.models import Consent, ConsentEvent, InboundEvent, OutboundLog, Recipient


def test_audit_models_are_read_only_in_admin():
    for model in (Recipient, Consent, ConsentEvent, InboundEvent, OutboundLog):
        model_admin = admin.site._registry[model]
        assert model_admin.has_add_permission(None) is False
        assert model_admin.has_change_permission(None) is False
        assert model_admin.has_delete_permission(None) is False
