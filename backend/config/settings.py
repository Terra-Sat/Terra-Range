from pathlib import Path
from urllib.parse import urlparse

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR.parent / ".env")

DATABASE_URL = env("DATABASE_URL")
if not DATABASE_URL:
    raise ImproperlyConfigured("DATABASE_URL must be set to the Aiven Postgres connection string.")
parsed_db = urlparse(DATABASE_URL)

SECRET_KEY = "django-insecure-change-me"
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.geography",
    "apps.observations",
    "apps.assessments",
    "apps.fieldwork",
    "apps.restricted",
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

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR.parent / "frontend"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

db_config = env.db("DATABASE_URL")
db_config["ENGINE"] = "django.contrib.gis.db.backends.postgis"
if parsed_db.hostname not in {None, "localhost", "127.0.0.1", "postgis"}:
    db_config["OPTIONS"] = {"sslmode": "require"}

DATABASES = {"default": db_config}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR.parent / "frontend"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Messaging (SMS) ---
MESSAGING_SMS_PROVIDER = env("MESSAGING_SMS_PROVIDER", default="fake")  # "fake" or "africastalking"
MESSAGING_CONSENT_POLICY_VERSION = env("MESSAGING_CONSENT_POLICY_VERSION", default="v1")
MESSAGING_CONSENT_PENDING_TTL_HOURS = env.int("MESSAGING_CONSENT_PENDING_TTL_HOURS", default=72)
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
