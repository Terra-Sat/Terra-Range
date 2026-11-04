"""Settings for running the messaging service on its own (registration, consent, SMS webhooks).

It needs only a PostgreSQL database (no PostGIS) and none of the mapping or data-pipeline code.
Everything comes from environment variables or the repo-root .env file; nothing secret lives here.

    DJANGO_SETTINGS_MODULE=config.settings_messaging gunicorn config.wsgi

Required in production: DJANGO_SECRET_KEY, DJANGO_ALLOWED_HOSTS, DATABASE_URL, and the AT_* values.
"""
from pathlib import Path
from urllib.parse import urlparse

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR.parent / ".env")

DEBUG = env.bool("DJANGO_DEBUG", default=False)

SECRET_KEY = env("DJANGO_SECRET_KEY", default="")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set. Generate one: python -c 'import secrets; print(secrets.token_urlsafe(50))'")
    SECRET_KEY = "development-only-not-a-secret"

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"] if DEBUG else [])
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must list the public host name(s), comma separated.")
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.messaging",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls_messaging"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

DATABASE_URL = env("DATABASE_URL", default="sqlite:///messaging.sqlite3" if DEBUG else "")
if not DATABASE_URL:
    raise ImproperlyConfigured("DATABASE_URL must be set to the PostgreSQL connection string.")
DATABASES = {"default": env.db_url_config(DATABASE_URL)}
_db_host = urlparse(DATABASE_URL).hostname
if DATABASES["default"]["ENGINE"].endswith("postgresql") and _db_host not in {None, "localhost", "127.0.0.1"}:
    DATABASES["default"]["OPTIONS"] = {"sslmode": env("DATABASE_SSLMODE", default="require")}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DATABASE_CONN_MAX_AGE", default=60)

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"  # python manage.py collectstatic, then serve from the reverse proxy
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Transport security (only when DEBUG is off) ---
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
if env.bool("DJANGO_BEHIND_TLS_PROXY", default=False):
    # Only enable when a reverse proxy you control terminates TLS and sets X-Forwarded-Proto.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("DJANGO_SSL_REDIRECT", default=False)

# --- Logging: stdout only. The messaging code never logs message text, API keys or full phone numbers. ---
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
    # Request logs include the callback URL path, which contains the webhook token: keep them out of the logs.
    "loggers": {"django.server": {"level": "WARNING"}},
}

# --- Messaging ---
MESSAGING_SMS_PROVIDER = env("MESSAGING_SMS_PROVIDER", default="fake")  # "fake" or "africastalking"
MESSAGING_CONSENT_POLICY_VERSION = env("MESSAGING_CONSENT_POLICY_VERSION", default="v1")
MESSAGING_CONSENT_PENDING_TTL_HOURS = env.int("MESSAGING_CONSENT_PENDING_TTL_HOURS", default=72)
MESSAGING_LOCATION_SMS_MAX_CHARS = env.int("MESSAGING_LOCATION_SMS_MAX_CHARS", default=300)
MESSAGING_LOCATION_MAX_OPTIONS = env.int("MESSAGING_LOCATION_MAX_OPTIONS", default=9)
AT_USERNAME = env("AT_USERNAME", default="")
AT_API_KEY = env("AT_API_KEY", default="")
AT_BASE_URL = env("AT_BASE_URL", default="https://api.africastalking.com")
AT_SENDER_ID = env("AT_SENDER_ID", default="")
AT_WEBHOOK_TOKEN = env("AT_WEBHOOK_TOKEN", default="")

META_WHATSAPP_API_VERSION = env("META_WHATSAPP_API_VERSION", default="v20.0")
META_WHATSAPP_PHONE_NUMBER_ID = env("META_WHATSAPP_PHONE_NUMBER_ID", default="")
META_WHATSAPP_ACCESS_TOKEN = env("META_WHATSAPP_ACCESS_TOKEN", default="")
META_WHATSAPP_APP_SECRET = env("META_WHATSAPP_APP_SECRET", default="")
META_WHATSAPP_VERIFY_TOKEN = env("META_WHATSAPP_VERIFY_TOKEN", default="")
