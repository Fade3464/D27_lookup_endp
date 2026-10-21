import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-do-not-use-in-production")
os.environ.setdefault("DB_PASSWORD", "test-only")
from .settings import *  # noqa: F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
ALLOWED_HOSTS = ["testserver", "localhost"]
