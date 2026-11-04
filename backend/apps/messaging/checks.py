"""Startup configuration checks. They run on runserver, migrate and ``manage.py check --deploy``.

A misconfigured provider is caught here, at start-up, instead of as a stream of failed SMS later.
"""
from django.conf import settings
from django.core.checks import Error, Warning, register

SANDBOX_USERNAME = "sandbox"
VALID_PROVIDERS = {"fake", "africastalking"}


@register()
def messaging_configuration(app_configs, **kwargs):
    problems = []
    provider = getattr(settings, "MESSAGING_SMS_PROVIDER", "fake")
    debug = bool(getattr(settings, "DEBUG", False))

    if provider not in VALID_PROVIDERS:
        problems.append(Error("MESSAGING_SMS_PROVIDER must be 'fake' or 'africastalking'.", id="messaging.E001"))
        return problems

    if provider == "fake":
        if not debug:
            problems.append(Warning(
                "MESSAGING_SMS_PROVIDER is 'fake': no real SMS will be sent.", id="messaging.W001",
                hint="Set MESSAGING_SMS_PROVIDER=africastalking and the AT_* values in the .env file.",
            ))
        return problems

    username = getattr(settings, "AT_USERNAME", "")
    api_key = getattr(settings, "AT_API_KEY", "")
    token = getattr(settings, "AT_WEBHOOK_TOKEN", "")
    base_url = getattr(settings, "AT_BASE_URL", "")

    if not username or not api_key:
        problems.append(Error(
            "Africa's Talking is selected but AT_USERNAME or AT_API_KEY is empty.", id="messaging.E002",
            hint="Set both in the .env file (see apps/messaging/README.md).",
        ))
    if not token:
        make = Warning if debug else Error
        problems.append(make(
            "AT_WEBHOOK_TOKEN is empty, so every Africa's Talking callback will be rejected with 403.",
            id="messaging.E003" if not debug else "messaging.W002",
            hint="Generate one with: openssl rand -hex 32",
        ))
    elif len(token) < 32:
        problems.append(Warning(
            "AT_WEBHOOK_TOKEN is shorter than 32 characters. It is the only protection on the callback URLs.",
            id="messaging.W003", hint="Generate one with: openssl rand -hex 32",
        ))
    sandbox_url = "sandbox" in base_url
    if username and (username == SANDBOX_USERNAME) != sandbox_url:
        problems.append(Warning(
            "AT_USERNAME and AT_BASE_URL point at different Africa's Talking environments (sandbox vs live).",
            id="messaging.W004",
            hint="Live: your app username with https://api.africastalking.com. "
                 "Sandbox: username 'sandbox' with https://api.sandbox.africastalking.com.",
        ))
    return problems
