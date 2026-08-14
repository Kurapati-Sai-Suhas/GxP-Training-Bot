from pathlib import Path
from urllib.parse import urlparse
import os
import sys

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# Throttling is disabled under the test runner: DRF throttle state lives in the cache and
# persists across test methods in one process, so real limits would make unrelated tests
# fail depending on execution order. The throttles themselves are tested explicitly with
# override_settings + a cleared cache (see accounts/tests.py::ThrottlingTests).
TESTING = "test" in sys.argv

DEBUG = os.getenv("DEBUG", "True") == "True"

SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-secret-key")
# Fail loudly rather than silently running production on the shared development key. A
# forgeable SECRET_KEY means forgeable sessions and password-reset tokens, and the previous
# default made "forgot to set it" indistinguishable from a correct deployment.
if not DEBUG and not TESTING and SECRET_KEY == "dev-only-secret-key":
    raise ImproperlyConfigured(
        "SECRET_KEY must be set to a unique value when DEBUG=False. "
        "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(50))\""
    )

ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")]
if not DEBUG and not TESTING and ("*" in ALLOWED_HOSTS or not any(ALLOWED_HOSTS)):
    raise ImproperlyConfigured("ALLOWED_HOSTS must list explicit hostnames when DEBUG=False.")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "corsheaders",
    "accounts",
    "sops",
    "quiz",
    "attempts",
    "ai_engine",
    "analytics",
    "audit",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # Serves the admin/DRF static assets under gunicorn, which -- unlike runserver -- does
    # not serve them itself. Without this the Django admin renders unstyled in production.
    "whitenoise.middleware.WhiteNoiseMiddleware",
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
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


def database_config():
    database_url = os.getenv("DATABASE_URL", "sqlite:///db.sqlite3")
    parsed = urlparse(database_url)
    if parsed.scheme == "sqlite":
        db_name = parsed.path.lstrip("/") or "db.sqlite3"
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / db_name}
    if parsed.scheme in {"postgres", "postgresql"}:
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": parsed.path.lstrip("/"),
            "USER": parsed.username,
            "PASSWORD": parsed.password,
            "HOST": parsed.hostname,
            "PORT": parsed.port or 5432,
        }
    raise ValueError(f"Unsupported DATABASE_URL scheme: {parsed.scheme}")


DATABASES = {"default": database_config()}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
# MEDIA_URL is retained because FileField uses it to build stored paths, but it is NOT
# routed in config/urls.py -- uploaded SOPs are controlled documents and are served only
# through the authenticated download endpoint.
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Defaults to eager (synchronous, in-process) so `manage.py test` and local dev without a
# Redis broker running keep working unchanged. Set CELERY_TASK_ALWAYS_EAGER=False (and run
# `celery -A config worker`) to actually offload SOP processing / AI generation to a worker.
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_ALWAYS_EAGER = os.getenv("CELERY_TASK_ALWAYS_EAGER", "True") == "True"
CELERY_TASK_EAGER_PROPAGATES = True

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
    # Applied per-endpoint via the classes in accounts/throttling.py, not globally --
    # see that module for why each of these four is limited.
    "DEFAULT_THROTTLE_RATES": {
        "login": None if TESTING else os.getenv("THROTTLE_LOGIN", "10/min"),
        "esignature": None if TESTING else os.getenv("THROTTLE_ESIGNATURE", "20/min"),
        "ai_generate": None if TESTING else os.getenv("THROTTLE_AI_GENERATE", "30/hour"),
        "sop_chat": None if TESTING else os.getenv("THROTTLE_SOP_CHAT", "60/hour"),
    },
}

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]

# --- Transport / cookie hardening -----------------------------------------------------
# Applied only when DEBUG is off, so local http://localhost development is unaffected:
# switching these on in development would make the session cookie unusable over plain HTTP
# and redirect every request to an https:// port that isn't listening.
if not DEBUG:
    SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "True") == "True"
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    # HSTS is intentionally opt-in: switching it on for a hostname served over plain HTTP
    # locks browsers out of it for the max-age duration, which is not something a deploy
    # should do by accident.
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "0"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = os.getenv("SECURE_HSTS_INCLUDE_SUBDOMAINS", "False") == "True"
    SECURE_HSTS_PRELOAD = os.getenv("SECURE_HSTS_PRELOAD", "False") == "True"
    # Behind a TLS-terminating proxy Django sees plain HTTP; this header is how the proxy
    # tells it the original request was secure. Only trust it when explicitly enabled --
    # trusting it unconditionally lets a client spoof "I'm on HTTPS".
    if os.getenv("USE_X_FORWARDED_PROTO", "False") == "True":
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    CSRF_TRUSTED_ORIGINS = [
        origin.strip()
        for origin in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",")
        if origin.strip()
    ]

# --- Logging ---------------------------------------------------------------------------
# There was previously no logging configuration at all, so every failure either surfaced to
# the user or vanished -- including LLM errors, which are swallowed by design to trigger the
# offline fallback. Console handler only: container platforms collect stdout, and a file
# handler would need volume management the deployment doesn't have.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "{asctime} {levelname} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        # Application loggers: ai_engine records provider failures and fallback use, sops
        # records extraction/chunking outcomes.
        "ai_engine": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "sops": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}
