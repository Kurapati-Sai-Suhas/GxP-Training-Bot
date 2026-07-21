from django.apps import AppConfig


class AttemptsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "attempts"

    def ready(self):
        from . import signals  # noqa: F401
