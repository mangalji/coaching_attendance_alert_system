import os
import sys
from pathlib import Path
from .database import build_database_config

BASE_DIR = Path(__file__).resolve().parent.parent
# Load optional local env without third-party dependency
p = BASE_DIR / ".env"
if p.exists():
    for line in p.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY", "dev-only-insecure-key-change-before-deploy"
)
DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() == "true"
if not DEBUG and SECRET_KEY.startswith("dev-only-insecure-key"):
    raise RuntimeError("Set DJANGO_SECRET_KEY when DJANGO_DEBUG is False.")
ALLOWED_HOSTS = [
    x.strip()
    for x in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
]
CSRF_TRUSTED_ORIGINS = [
    x.strip()
    for x in os.getenv("CSRF_TRUSTED_ORIGINS", "http://localhost:8000").split(",")
]
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
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
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {"default": build_database_config(BASE_DIR)}
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "en-us"
TIME_ZONE = os.getenv("TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# The hashed manifest storage needs `collectstatic`; use plain storage in dev/tests.
_plain_static = DEBUG or "test" in sys.argv
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if _plain_static
        else "core.storage.LenientManifestStaticFilesStorage"
    },
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
# Pinned explicitly rather than left to Django's default, so a future Django upgrade
# can't silently change this app's cross-site cookie behaviour.
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
MIDDLEWARE.insert(
    MIDDLEWARE.index("django.contrib.auth.middleware.AuthenticationMiddleware") + 1,
    "core.middleware.VerifiedSessionMiddleware",
)
LOGIN_URL = "/"
LOGOUT_REDIRECT_URL = "/"
EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND",
    (
        "django.core.mail.backends.console.EmailBackend"
        if DEBUG
        else "django.core.mail.backends.smtp.EmailBackend"
    ),
)
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True").lower() == "true"
EMAIL_TIMEOUT = 15
DEFAULT_FROM_EMAIL = (
    os.getenv("DEFAULT_FROM_EMAIL") or EMAIL_HOST_USER or "portal@example.invalid"
)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000")
SESSION_COOKIE_AGE = 28800
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

if os.getenv("SECURE_PROXY_SSL_HEADER", "").lower() == "true":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
if not DEBUG and os.getenv("FORCE_HTTPS", "False").lower() == "true":
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000

# Country code prepended to 10-digit guardian numbers (E.164 for WhatsApp).
DEFAULT_COUNTRY_CODE = os.getenv("DEFAULT_COUNTRY_CODE", "91")
# Meta webhook (delivery receipts)
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")
WHITENOISE_USE_FINDERS = DEBUG or "test" in sys.argv

# Number of reverse proxies in front of the app (0 = none). Needed so login throttling sees the
# real client IP instead of the proxy's address.
NUM_PROXIES = int(os.getenv("NUM_PROXIES", "0"))
# Django's /admin/ is off in production unless explicitly enabled.
DJANGO_ADMIN_ENABLED = os.getenv("DJANGO_ADMIN_ENABLED", "True" if DEBUG else "False").lower() == "true"
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
CSRF_FAILURE_VIEW = "core.views.csrf_failure"

# Errors must be visible in production logs (container stdout / journald).
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

# A bulk student-import CSV for a large institute can run a few MB; raise Django's
# conservative defaults so a legitimate file isn't rejected before validation runs.
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("DATA_UPLOAD_MAX_MEMORY_SIZE", str(10 * 1024 * 1024)))
FILE_UPLOAD_MAX_MEMORY_SIZE = DATA_UPLOAD_MAX_MEMORY_SIZE

# Rows per page on list/report screens. Tune per deployment via env if an institute
# wants denser or lighter pages; the query count on these pages does not change
# with institute size, only with this number.
LIST_PAGE_SIZE = int(os.getenv("LIST_PAGE_SIZE", "50"))
REPORT_PAGE_SIZE = int(os.getenv("REPORT_PAGE_SIZE", "100"))
