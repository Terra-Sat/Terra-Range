import json
import logging

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.messaging.inbound import handle_delivery_report, handle_inbound_sms
from apps.messaging.providers.africastalking import AfricasTalkingSms
from apps.messaging.providers.base import WebhookParseError
from apps.messaging.providers.meta_whatsapp import MetaWhatsAppProvider

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


@csrf_exempt
def meta_whatsapp_webhook(request, token):
    provider = MetaWhatsAppProvider.from_settings()
    if request.method == "GET":
        mode = request.GET.get("hub.mode", "")
        verify_token = request.GET.get("hub.verify_token", "")
        challenge = request.GET.get("hub.challenge", "")
        if provider.verify_webhook(mode=mode, token=verify_token, challenge=challenge):
            return HttpResponse(challenge, status=200)
        return HttpResponse("Forbidden", status=403)

    if request.method != "POST":
        return HttpResponse(status=405)

    signature = request.headers.get("X-Hub-Signature-256", "")
    if not provider.verify_webhook_signature(request.body, signature):
        logger.warning("meta whatsapp webhook rejected: invalid signature")
        return HttpResponse(status=403)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (TypeError, ValueError):
        return HttpResponse("bad request", status=400)

    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                if "messages" in value:
                    message = provider.parse_inbound(payload)
                    handle_inbound_sms(message)
                    return HttpResponse("OK")
                if "statuses" in value:
                    status_event = provider.parse_status(payload)
                    handle_delivery_report(status_event)
                    return HttpResponse("OK")
    except WebhookParseError:
        return HttpResponse("bad request", status=400)
    return HttpResponse("OK")
