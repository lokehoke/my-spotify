"""Выпуск и отзыв JWT (ARCHITECTURE.md §7.4).

Два уровня отзыва, и это важно не путать:

* **цепочка** (claim `cid`) — одна последовательность refresh-токенов, порождённая
  одним входом. Гасится при выходе на устройстве и при детекте кражи;
* **все токены пользователя** (claim `tv`, поле User.token_version) — гасятся при
  смене пароля и при явном «выйти везде».

Access-токен намеренно НЕ проверяется по БД: он живёт 15 минут и валидируется
подписью без I/O. Отзыв убивает refresh-цепочки — доступ прекращается в пределах
времени жизни access-токена.
"""

import uuid

from django.db.models import F
from django.utils import timezone
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.state import token_backend
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

TOKEN_VERSION_CLAIM = "tv"
DEVICE_CLAIM = "device_id"
CHAIN_CLAIM = "cid"

# Клиент может отправить два refresh-запроса с одним токеном (ретрай после
# таймаута, возврат приложения из фона несколькими запросами сразу). В этом окне
# повтор считается гонкой, а не кражей: отвечаем 401, но ничего не отзываем.
REUSE_GRACE_SECONDS = 10


def access_lifetime_seconds() -> int:
    return int(jwt_settings.ACCESS_TOKEN_LIFETIME.total_seconds())


def build_refresh(user, device=None) -> RefreshToken:
    refresh = RefreshToken.for_user(user)
    refresh[TOKEN_VERSION_CLAIM] = user.token_version
    refresh[CHAIN_CLAIM] = uuid.uuid4().hex
    if device is not None:
        refresh[DEVICE_CLAIM] = device.id
    return refresh


def issue_tokens(user, device=None) -> dict:
    refresh = build_refresh(user, device=device)
    return {
        "refresh": str(refresh),
        "access": str(refresh.access_token),
        "access_expires_in": access_lifetime_seconds(),
    }


def decode_refresh_payload(raw_token: str) -> dict | None:
    """Payload с проверкой подписи и срока, но БЕЗ проверки блеклиста.

    Нужен, чтобы отличить «предъявлен ротированный токен» (подпись валидна, jti
    в блеклисте) от мусорного токена до того, как simplejwt бросит ошибку.
    """
    try:
        return token_backend.decode(raw_token, verify=True)
    except Exception:
        return None


def get_blacklist_entry(payload: dict) -> BlacklistedToken | None:
    jti = payload.get(jwt_settings.JTI_CLAIM)
    if not jti:
        return None
    return BlacklistedToken.objects.filter(token__jti=jti).first()


def is_recent_blacklist(entry: BlacklistedToken) -> bool:
    return (timezone.now() - entry.blacklisted_at).total_seconds() <= REUSE_GRACE_SECONDS


def revoke_chain(*, user, chain_id, reason) -> None:
    """Отзыв одной цепочки: остальные сессии пользователя не затрагиваются."""
    if not chain_id:
        return
    from apps.users.models import RevokedTokenChain

    RevokedTokenChain.objects.get_or_create(
        chain_id=chain_id,
        defaults={
            "user": user,
            "reason": reason,
            "expires_at": timezone.now() + jwt_settings.REFRESH_TOKEN_LIFETIME,
        },
    )


def is_chain_revoked(chain_id) -> bool:
    if not chain_id:
        return False
    from apps.users.models import RevokedTokenChain

    return RevokedTokenChain.objects.filter(chain_id=chain_id).exists()


def revoke_all_tokens(user) -> None:
    """Инвалидация всех refresh-цепочек пользователя.

    Инкремент token_version — основной механизм: он работает и для токенов,
    появившихся при ротации, которых нет в OutstandingToken. Дополнительно
    блеклистим известные outstanding-токены, чтобы таблица отражала отзыв.
    """
    type(user).objects.filter(pk=user.pk).update(token_version=F("token_version") + 1)
    user.refresh_from_db(fields=["token_version"])

    pending = OutstandingToken.objects.filter(user=user, blacklistedtoken__isnull=True)
    BlacklistedToken.objects.bulk_create(
        [BlacklistedToken(token_id=pk) for pk in pending.values_list("id", flat=True)],
        ignore_conflicts=True,
    )
