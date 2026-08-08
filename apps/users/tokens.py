"""Выпуск и валидация JWT (ARCHITECTURE.md §7.4).

Инвариант: refresh несёт claim `tv` (версия токенов пользователя) и, если вход
был с устройства, `device_id`. Оба claim'а переживают ротацию — simplejwt
переиспользует payload, меняя только jti/exp/iat.

Access-токен намеренно НЕ проверяется по БД: он живёт 15 минут и валидируется
подписью без I/O. Отзыв (смена пароля, logout-all) убивает refresh-цепочки —
доступ прекращается в пределах времени жизни access-токена.
"""

from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.db.models import F
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.state import token_backend
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

TOKEN_VERSION_CLAIM = "tv"
DEVICE_CLAIM = "device_id"

EMAIL_VERIFY_SALT = "users.email-verify"
EMAIL_VERIFY_TTL = 60 * 60 * 24  # 24 часа


def access_lifetime_seconds() -> int:
    return int(jwt_settings.ACCESS_TOKEN_LIFETIME.total_seconds())


def issue_tokens(user, device=None) -> dict:
    """Пара access+refresh с актуальными claim'ами."""
    refresh = RefreshToken.for_user(user)
    refresh[TOKEN_VERSION_CLAIM] = user.token_version
    if device is not None:
        refresh[DEVICE_CLAIM] = device.id
    return {
        "refresh": str(refresh),
        "access": str(refresh.access_token),
        "access_expires_in": access_lifetime_seconds(),
    }


def decode_refresh_payload(raw_token: str) -> dict | None:
    """Payload с проверкой подписи и срока, но БЕЗ проверки блеклиста.

    Нужен, чтобы отличить «переиспользован ротированный токен» (payload валиден,
    jti в блеклисте) от «мусорный токен» до того, как simplejwt бросит ошибку.
    """
    try:
        return token_backend.decode(raw_token, verify=True)
    except Exception:
        return None


def is_blacklisted(payload: dict) -> bool:
    jti = payload.get(jwt_settings.JTI_CLAIM)
    if not jti:
        return False
    return BlacklistedToken.objects.filter(token__jti=jti).exists()


def revoke_all_tokens(user) -> None:
    """Инвалидация всех refresh-цепочек пользователя.

    Инкремент token_version — основной механизм (работает и для токенов,
    появившихся при ротации, которых нет в OutstandingToken). Дополнительно
    блеклистим известные outstanding-токены, чтобы таблица отражала отзыв.
    """
    type(user).objects.filter(pk=user.pk).update(token_version=F("token_version") + 1)
    user.refresh_from_db(fields=["token_version"])
    for outstanding in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=outstanding)


def make_email_verify_token(user) -> str:
    return TimestampSigner(salt=EMAIL_VERIFY_SALT).sign(str(user.public_id))


def read_email_verify_token(token: str) -> str | None:
    """Возвращает public_id пользователя или None, если токен битый/протух."""
    try:
        return TimestampSigner(salt=EMAIL_VERIFY_SALT).unsign(token, max_age=EMAIL_VERIFY_TTL)
    except (BadSignature, SignatureExpired):
        return None
