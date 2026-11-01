"""Settings for the messaging tests: SQLite, no PostGIS, no network, no secrets."""
SECRET_KEY = "test-only-not-a-secret"
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "apps.messaging",
]
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
ROOT_URLCONF = "config.urls_messaging_test"
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
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
USE_TZ = True
TIME_ZONE = "UTC"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

MESSAGING_SMS_PROVIDER = "fake"
MESSAGING_CONSENT_POLICY_VERSION = "v1"
MESSAGING_CONSENT_PENDING_TTL_HOURS = 72
AT_USERNAME = "sandbox"
AT_API_KEY = "test-api-key-123"
AT_BASE_URL = "https://api.sandbox.africastalking.com"
AT_SENDER_ID = ""
AT_WEBHOOK_TOKEN = "test-webhook-token"
