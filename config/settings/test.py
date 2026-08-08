from .base import *  # noqa: F403

SECRET_KEY = env("SECRET_KEY", default="test-only-secret-key")  # noqa: F405
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]

# Быстрый хэшер — тесты создают много пользователей
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Тесты не должны зависеть от поднятых Redis: локальный кэш и синхронные задачи
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
