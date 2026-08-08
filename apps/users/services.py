"""Публичный API приложения users: вся запись — только через эти функции."""

from django.db import transaction
from django.utils import timezone

from apps.users import tokens
from apps.users.models import User, UserDevice, UserProfile


@transaction.atomic
def register_user(*, email: str, password: str, display_name: str) -> User:
    return User.objects.create_user(email=email, password=password, display_name=display_name)


@transaction.atomic
def change_password(*, user: User, new_password: str) -> None:
    """Смена пароля отзывает все refresh-цепочки: украденная сессия не переживает
    смену пароля владельцем. Текущему клиенту вьюха выдаёт свежую пару токенов.
    """
    user.set_password(new_password)
    user.save(update_fields=["password"])
    tokens.revoke_all_tokens(user)


@transaction.atomic
def confirm_email(*, user: User) -> User:
    if user.email_verified_at is None:
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified_at"])
    return user


def logout_everywhere(*, user: User) -> None:
    tokens.revoke_all_tokens(user)


@transaction.atomic
def delete_account(*, user: User) -> None:
    """Удаление аккаунта (152-ФЗ/GDPR).

    Подписки удаляются явно: FK стоит на PROTECT, чтобы платёжная история не
    исчезала по случайному каскаду, но запрос на удаление аккаунта — это как раз
    осознанное удаление всего.
    """
    tokens.revoke_all_tokens(user)
    user.subscriptions.all().delete()
    user.devices.all().delete()
    user.delete()


@transaction.atomic
def register_device(
    *,
    user: User,
    fingerprint,
    kind: str,
    name: str = "",
    app_version: str = "",
    ip: str | None = None,
) -> UserDevice:
    """Идемпотентная регистрация устройства при логине.

    Повторный логин на отозванном устройстве снимает отзыв: отзыв убивает
    старые refresh-токены, а новый вход по паролю — новое согласие владельца.
    """
    defaults: dict = {"kind": kind, "last_seen_at": timezone.now(), "revoked_at": None}
    if name:
        defaults["name"] = name
    if app_version:
        defaults["app_version"] = app_version
    if ip:
        defaults["last_ip"] = ip
    device, _ = UserDevice.objects.update_or_create(
        user=user, fingerprint=fingerprint, defaults=defaults
    )
    return device


def revoke_device(*, device: UserDevice) -> UserDevice:
    """Идемпотентный отзыв: refresh-токены с этим device_id перестают обновляться."""
    if device.revoked_at is None:
        device.revoked_at = timezone.now()
        device.save(update_fields=["revoked_at"])
    return device


def touch_device(*, device_id: int) -> None:
    UserDevice.objects.filter(id=device_id).update(last_seen_at=timezone.now())


def update_profile(*, profile: UserProfile, **fields) -> UserProfile:
    for name, value in fields.items():
        setattr(profile, name, value)
    profile.save()
    return profile
