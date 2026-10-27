from django.apps import AppConfig


class RestrictedConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.restricted"
