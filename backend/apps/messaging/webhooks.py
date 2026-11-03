import logging

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.messaging.inbound import handle_delivery_report, handle_inbound_sms
from apps.messaging.providers.africastalking import AfricasTalkingSms
from apps.messaging.providers.base import WebhookParseError

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def africastalking_inbound(request, token):
    provider = AfricasTalkingSms.from_settings()
    if not provider.verify_webhook(url_token=token):
        logger.warning("africastalking inbound webhook rejected: invalid token")
        return HttpResponse(status=403)
    try:
        message = provider.parse_inbound(request.POST)
    except WebhookParseError:
        return HttpResponse("bad request", status=400)
    handle_inbound_sms(message)
    return HttpResponse("OK")


@csrf_exempt
@require_POST
def africastalking_delivery(request, token):
    provider = AfricasTalkingSms.from_settings()
    if not provider.verify_webhook(url_token=token):
        logger.warning("africastalking delivery webhook rejected: invalid token")
        return HttpResponse(status=403)
    try:
        status_event = provider.parse_status(request.POST)
    except WebhookParseError:
        return HttpResponse("bad request", status=400)
    handle_delivery_report(status_event)
    return HttpResponse("OK")
