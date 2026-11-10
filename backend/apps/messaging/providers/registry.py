from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from apps.messaging.providers.africastalking import AfricasTalkingSms
from apps.messaging.providers.base import SmsProvider
from apps.messaging.providers.fake import FakeSmsProvider
from apps.messaging.providers.meta_whatsapp import MetaWhatsAppProvider


def get_sms_provider() -> SmsProvider:
    name = settings.MESSAGING_SMS_PROVIDER
    if name == "fake":
        return FakeSmsProvider()
    if name == "africastalking":
        return AfricasTalkingSms.from_settings()
    raise ImproperlyConfigured("MESSAGING_SMS_PROVIDER must be 'fake' or 'africastalking'.")


def get_whatsapp_provider():
    if settings.META_WHATSAPP_ACCESS_TOKEN and settings.META_WHATSAPP_PHONE_NUMBER_ID:
        return MetaWhatsAppProvider.from_settings()
    raise ImproperlyConfigured("Meta WhatsApp is not configured. Set META_WHATSAPP_ACCESS_TOKEN and META_WHATSAPP_PHONE_NUMBER_ID.")
