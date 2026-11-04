"""WSGI entry point. The messaging service runs with:

    DJANGO_SETTINGS_MODULE=config.settings_messaging gunicorn config.wsgi
"""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = get_wsgi_application()

if os.environ["DJANGO_SETTINGS_MODULE"].endswith("settings_messaging"):
    # Fail fast: a messaging service with a missing key or webhook token must not start and look healthy.
    from django.core.management import call_command

    call_command("check")
