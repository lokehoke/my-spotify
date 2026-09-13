"""Отзыв токенов: §7.4 — ротация, детект переиспользования, смена пароля, logout-all.

Ключевое разделение: выход на устройстве и детект кражи гасят ОДНУ цепочку,
смена/сброс пароля и logout-all — ВСЕ сессии пользователя.
"""

import pytest
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken

from apps.users.models import ChainRevocationReason, RevokedTokenChain, UserDevice
from apps.users.tokens import REUSE_GRACE_SECONDS
from conftest import PASSWORD

pytestmark = pytest.mark.django_db

REFRESH_URL = "/api/v1/auth/token/refresh"
TOKEN_URL = "/api/v1/auth/token"

DEVICE = {
    "fingerprint": "3f1c9a1e-6a1b-4f0e-9d2c-8b7a5c4d3e2f",
    "kind": "android",
    "name": "Pixel 9",
}


def age_blacklist_entries():
    """Состарить записи блеклиста, чтобы выйти за грейс-окно гонки."""
    BlacklistedToken.objects.update(
        blacklisted_at=timezone.now() - timezone.timedelta(seconds=REUSE_GRACE_SECONDS + 5)
    )


def login(api_client, user, **extra):
    return api_client.post(
        TOKEN_URL, {"username": user.username, "password": PASSWORD, **extra}, format="json"
    ).data


def test_reuse_of_rotated_refresh_kills_only_its_own_chain(api_client, user):
    """Кража токена гасит свою цепочку, но не выкидывает пользователя с других устройств."""
    stolen = login(api_client, user)
    other_device = login(api_client, user)  # независимый вход, своя цепочка

    rotated = api_client.post(REFRESH_URL, {"refresh": stolen["refresh"]}, format="json")
    assert rotated.status_code == 200
    age_blacklist_entries()

    replay = api_client.post(REFRESH_URL, {"refresh": stolen["refresh"]}, format="json")
    assert replay.status_code == 401
    assert replay.data["error"]["code"] == "token_reuse_detected"

    # скомпрометированная цепочка мертва целиком
    victim = api_client.post(REFRESH_URL, {"refresh": rotated.data["refresh"]}, format="json")
    assert victim.status_code == 401
    assert victim.data["error"]["code"] == "token_revoked"

    # а вторая сессия продолжает работать
    survivor = api_client.post(REFRESH_URL, {"refresh": other_device["refresh"]}, format="json")
    assert survivor.status_code == 200

    revocation = RevokedTokenChain.objects.get()
    assert revocation.reason == ChainRevocationReason.REUSE


def test_parallel_refresh_race_is_not_treated_as_theft(api_client, user):
    """Клиент может отправить два refresh с одним токеном (ретрай, возврат из фона).
    Это гонка, а не кража: отказ есть, но сессии остаются живыми."""
    tokens = login(api_client, user)

    first = api_client.post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json")
    assert first.status_code == 200

    racy = api_client.post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json")
    assert racy.status_code == 401
    assert racy.data["error"]["code"] == "token_not_valid"
    assert not RevokedTokenChain.objects.exists()

    # выданный первым запросом токен по-прежнему рабочий
    assert (
        api_client.post(REFRESH_URL, {"refresh": first.data["refresh"]}, format="json").status_code
        == 200
    )


def test_logout_does_not_look_like_theft(api_client, user):
    """Штатный выход не должен выкидывать пользователя с остальных устройств."""
    web = login(api_client, user)
    phone = login(api_client, user)

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {web['access']}")
    assert (
        api_client.post(
            "/api/v1/auth/logout", {"refresh": web["refresh"]}, format="json"
        ).status_code
        == 204
    )
    api_client.credentials()
    age_blacklist_entries()

    # повторный запрос с тем же токеном (in-flight/ретрай) — обычный отказ
    replay = api_client.post(REFRESH_URL, {"refresh": web["refresh"]}, format="json")
    assert replay.status_code == 401
    assert replay.data["error"]["code"] == "token_revoked"

    # телефон остаётся залогиненным
    assert (
        api_client.post(REFRESH_URL, {"refresh": phone["refresh"]}, format="json").status_code
        == 200
    )


def test_password_change_revokes_everything_and_issues_new_pair(api_client, auth_client, user):
    other = login(api_client, user)

    response = auth_client.post(
        "/api/v1/me/password",
        {"current_password": PASSWORD, "new_password": "N3w-Sup3r-pass!"},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["access"] and response.data["refresh"]

    for refresh in (other["refresh"],):
        stale = api_client.post(REFRESH_URL, {"refresh": refresh}, format="json")
        assert stale.status_code == 401
        assert stale.data["error"]["code"] == "token_revoked"

    # выданная взамен пара сразу рабочая — пользователя не выкидывает из приложения
    assert (
        api_client.post(
            REFRESH_URL, {"refresh": response.data["refresh"]}, format="json"
        ).status_code
        == 200
    )


def test_password_change_keeps_device_binding(api_client, user):
    """Новая пара после смены пароля обязана сохранить привязку к устройству,
    иначе сессию больше нельзя отозвать через /me/devices."""
    tokens = login(api_client, user, device=DEVICE)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    changed = api_client.post(
        "/api/v1/me/password",
        {"current_password": PASSWORD, "new_password": "N3w-Sup3r-pass!"},
        format="json",
    )
    assert changed.status_code == 200

    device = UserDevice.objects.get(user=user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {changed.data['access']}")
    assert api_client.delete(f"/api/v1/me/devices/{device.id}").status_code == 204
    api_client.credentials()

    response = api_client.post(REFRESH_URL, {"refresh": changed.data["refresh"]}, format="json")
    assert response.status_code == 401
    assert response.data["error"]["code"] == "device_revoked"


def test_logout_all_kills_every_session(api_client, user, auth_client, tokens):
    second = login(api_client, user)

    assert auth_client.post("/api/v1/auth/logout/all").status_code == 204

    for refresh in (tokens["refresh"], second["refresh"]):
        response = api_client.post(REFRESH_URL, {"refresh": refresh}, format="json")
        assert response.status_code == 401
        assert response.data["error"]["code"] == "token_revoked"


def test_logout_all_requires_auth(api_client):
    assert api_client.post("/api/v1/auth/logout/all").status_code == 401


def test_device_claim_survives_rotation(api_client, user):
    """Отзыв устройства должен работать и после нескольких ротаций refresh."""
    refresh = login(api_client, user, device=DEVICE)["refresh"]

    for _ in range(2):
        rotated = api_client.post(REFRESH_URL, {"refresh": refresh}, format="json")
        assert rotated.status_code == 200
        refresh = rotated.data["refresh"]

    device = UserDevice.objects.get(user=user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {rotated.data['access']}")
    assert api_client.delete(f"/api/v1/me/devices/{device.id}").status_code == 204
    api_client.credentials()

    response = api_client.post(REFRESH_URL, {"refresh": refresh}, format="json")
    assert response.status_code == 401
    assert response.data["error"]["code"] == "device_revoked"


def test_refresh_of_deleted_user_rejected(api_client, user, tokens, auth_client):
    auth_client.delete("/api/v1/me", {"password": PASSWORD}, format="json")
    response = api_client.post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json")
    assert response.status_code == 401


def test_refresh_endpoint_is_throttled(api_client):
    """Scope auth (10/min) висит и на refresh — иначе эндпоинт открыт для перебора."""
    for _ in range(10):
        api_client.post(REFRESH_URL, {"refresh": "garbage"}, format="json")
    response = api_client.post(REFRESH_URL, {"refresh": "garbage"}, format="json")
    assert response.status_code == 429


def test_flush_expired_tokens_task(user):
    """Ночная задача чистит протухшие отзывы цепочек (§7.4)."""
    from apps.users.tasks import flush_expired_tokens

    RevokedTokenChain.objects.create(
        chain_id="11111111-1111-1111-1111-111111111111",
        user=user,
        reason=ChainRevocationReason.LOGOUT,
        expires_at=timezone.now() - timezone.timedelta(days=1),
    )
    fresh = RevokedTokenChain.objects.create(
        chain_id="22222222-2222-2222-2222-222222222222",
        user=user,
        reason=ChainRevocationReason.REUSE,
        expires_at=timezone.now() + timezone.timedelta(days=1),
    )

    flush_expired_tokens()

    assert list(RevokedTokenChain.objects.all()) == [fresh]
