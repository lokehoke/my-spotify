"""Сборка писем. Отправка — через Celery-задачи (apps/users/tasks.py)."""

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.users import tokens


def email_verification_message(user) -> tuple[str, str]:
    token = tokens.make_email_verify_token(user)
    link = f"{settings.FRONTEND_URL}/verify-email?token={token}"
    subject = "Подтверждение email"
    body = (
        f"Здравствуйте!\n\n"
        f"Подтвердите адрес почты, перейдя по ссылке:\n{link}\n\n"
        f"Ссылка действует 24 часа. Если вы не регистрировались — просто "
        f"проигнорируйте это письмо."
    )
    return subject, body


def password_reset_message(user) -> tuple[str, str]:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    link = f"{settings.FRONTEND_URL}/reset-password?uid={uid}&token={token}"
    subject = "Сброс пароля"
    body = (
        f"Здравствуйте!\n\n"
        f"Для установки нового пароля перейдите по ссылке:\n{link}\n\n"
        f"Если вы не запрашивали сброс пароля — просто проигнорируйте это письмо, "
        f"пароль останется прежним."
    )
    return subject, body
