from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from apps.messaging.providers.africastalking import AfricasTalkingSms
from apps.messaging.providers.base import SmsProvider
from apps.messaging.providers.fake import FakeSmsProvider


def get_sms_provider() -> SmsProvider:
    name = settings.MESSAGING_SMS_PROVIDER
    if name == "fake":
        return FakeSmsProvider()
    if name == "africastalking":
        return AfricasTalkingSms.from_settings()
    raise ImproperlyConfigured("MESSAGING_SMS_PROVIDER must be 'fake' or 'africastalking'.")
