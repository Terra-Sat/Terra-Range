from apps.messaging.providers.base import SendRequest, SendResult, SmsProvider


class FakeSmsProvider(SmsProvider):
    """In-memory provider for tests and local development. No network, no credentials."""

    name = "fake"
    outbox = []
    next_result = None
    raise_next = None

    @classmethod
    def reset(cls):
        cls.outbox = []
        cls.next_result = None
        cls.raise_next = None

    def send(self, request: SendRequest) -> SendResult:
        if FakeSmsProvider.raise_next is not None:
            exc, FakeSmsProvider.raise_next = FakeSmsProvider.raise_next, None
            raise exc
        if FakeSmsProvider.next_result is not None:
            result, FakeSmsProvider.next_result = FakeSmsProvider.next_result, None
            return result
        FakeSmsProvider.outbox.append(request)
        return SendResult(True, provider_message_id=f"fake-{len(FakeSmsProvider.outbox)}")
