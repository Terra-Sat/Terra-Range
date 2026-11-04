"""Read helpers for the delivery engine."""
from apps.messaging.enums import Channel, ConsentStatus
from apps.messaging.models import Location, Recipient


def active_recipients(*, ward_code=None, location_code=None, include_descendants=True, language=None, channel=Channel.SMS):
    """Recipients who currently have ACTIVE consent on the channel, optionally narrowed.

    location_code accepts a Location at any level. With include_descendants (the default) a county
    returns everyone registered anywhere below it. An unknown location_code returns nobody.
    ward_code is the exact-match filter older callers use.
    """
    queryset = Recipient.objects.filter(consents__channel=channel, consents__status=ConsentStatus.ACTIVE)
    if location_code:
        node = Location.objects.filter(code=location_code).first()
        if node is None:
            return Recipient.objects.none()
        if include_descendants:
            queryset = queryset.filter(location__path__startswith=node.path)
        else:
            queryset = queryset.filter(location=node)
    if ward_code:
        queryset = queryset.filter(ward_code=ward_code)
    if language:
        queryset = queryset.filter(language=language)
    return queryset.distinct()
