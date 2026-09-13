from .base import *  # noqa: F403

# SECRET_KEY и DATABASE_URL приходят из окружения: base.py требует их без
# дефолтов, поэтому переопределить их здесь уже поздно (см. Makefile и CI)
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]

# Быстрый хэшер — тесты создают много пользователей
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Тесты не должны зависеть от поднятых Redis: локальный кэш и синхронные задачи
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
