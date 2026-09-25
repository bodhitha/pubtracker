"""Settings are read from environment variables so the same image runs locally and on Kubernetes."""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
env = os.environ.get

SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-insecure-key")
DEBUG = env("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = [h for h in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]
CSRF_TRUSTED_ORIGINS = [o for o in env("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o]

# "local": Django's own login page (development).
# "proxy": oauth2-proxy signs people in and vouches for them with AUTH_PROXY_SECRET (production).
AUTH_MODE = env("AUTH_MODE", "local")
AUTH_PROXY_SECRET = env("AUTH_PROXY_SECRET", "")
if AUTH_MODE not in ("local", "proxy"):
    raise ImproperlyConfigured("AUTH_MODE must be 'local' or 'proxy'.")
if AUTH_MODE == "proxy" and len(AUTH_PROXY_SECRET) < 16:
    raise ImproperlyConfigured("AUTH_MODE=proxy needs AUTH_PROXY_SECRET (16+ characters), shared with oauth2-proxy.")

INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "pubs",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
if AUTH_MODE == "proxy":
    MIDDLEWARE.insert(MIDDLEWARE.index("django.contrib.auth.middleware.AuthenticationMiddleware") + 1,
                      "pubs.auth.ProxyAuthMiddleware")
    AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.RemoteUserBackend"]
MIDDLEWARE.append("django.contrib.auth.middleware.LoginRequiredMiddleware")  # every page needs a login

ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request", "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages", "pubs.views.site_context"]},
}]
WSGI_APPLICATION = "config.wsgi.application"

# SQLite in WAL mode: the web workers and the nightly jobs share one file on a persistent volume.
# IMMEDIATE transactions plus a busy timeout make concurrent writers wait instead of failing.
DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": env("SQLITE_PATH", str(BASE_DIR / "db.sqlite3")),
    "OPTIONS": {"transaction_mode": "IMMEDIATE", "timeout": 20,
                "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;"},
}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE, TIME_ZONE, USE_I18N, USE_TZ = "en-us", "America/Chicago", False, True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}}
LOGIN_URL, LOGIN_REDIRECT_URL, LOGOUT_REDIRECT_URL = "login", "work_list", "login"
SITE_TITLE = env("SITE_TITLE", "FNAL CMS group publications")
INSPIRE_URL = env("INSPIRE_URL", "https://inspirehep.net/api/literature")
INSPIRE_TIMEOUT = float(env("INSPIRE_TIMEOUT", "8"))

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = env("DJANGO_SECURE_COOKIES", "1") == "1"
LOGGING = {"version": 1, "handlers": {"console": {"class": "logging.StreamHandler"}},
           "root": {"handlers": ["console"], "level": "INFO"}}
