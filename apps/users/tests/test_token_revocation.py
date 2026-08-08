"""Отзыв токенов: §7.4 — ротация, детект переиспользования, смена пароля, logout-all."""

import pytest

from apps.users.models import UserDevice
from conftest import PASSWORD

pytestmark = pytest.mark.django_db

REFRESH_URL = "/api/v1/auth/token/refresh"
TOKEN_URL = "/api/v1/auth/token"

DEVICE = {
    "fingerprint": "3f1c9a1e-6a1b-4f0e-9d2c-8b7a5c4d3e2f",
    "kind": "android",
    "name": "Pixel 9",
}


def test_reuse_of_rotated_refresh_kills_whole_chain(api_client, tokens):
    """Переиспользование ротированного токена — признак кражи: умирает вся цепочка,
    а не только предъявленный токен."""
    rotated = api_client.post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json")
    assert rotated.status_code == 200
    fresh_refresh = rotated.data["refresh"]

    # злоумышленник предъявляет старый (уже ротированный) токен
    replay = api_client.post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json")
    assert replay.status_code == 401
    assert replay.data["error"]["code"] == "token_reuse_detected"

    # legitimate-токен, выданный при ротации, тоже больше не работает
    victim = api_client.post(REFRESH_URL, {"refresh": fresh_refresh}, format="json")
    assert victim.status_code == 401
    assert victim.data["error"]["code"] == "token_revoked"


def test_password_change_revokes_refresh_and_issues_new_pair(api_client, auth_client, tokens):
    response = auth_client.post(
        "/api/v1/me/password",
        {"current_password": PASSWORD, "new_password": "N3w-Sup3r-pass!"},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["access"] and response.data["refresh"]

    old = api_client.post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json")
    assert old.status_code == 401
    assert old.data["error"]["code"] == "token_revoked"

    # выданная взамен пара сразу рабочая — пользователя не выкидывает из приложения
    new = api_client.post(REFRESH_URL, {"refresh": response.data["refresh"]}, format="json")
    assert new.status_code == 200


def test_logout_all_kills_every_session(api_client, user, auth_client, tokens):
    second = api_client.post(
        TOKEN_URL, {"email": user.email, "password": PASSWORD}, format="json"
    ).data

    assert auth_client.post("/api/v1/auth/logout/all").status_code == 204

    for refresh in (tokens["refresh"], second["refresh"]):
        response = api_client.post(REFRESH_URL, {"refresh": refresh}, format="json")
        assert response.status_code == 401
        assert response.data["error"]["code"] == "token_revoked"


def test_logout_all_requires_auth(api_client):
    assert api_client.post("/api/v1/auth/logout/all").status_code == 401


def test_device_claim_survives_rotation(api_client, user):
    """Отзыв устройства должен работать и после нескольких ротаций refresh."""
    login = api_client.post(
        TOKEN_URL,
        {"email": user.email, "password": PASSWORD, "device": DEVICE},
        format="json",
    )
    refresh = login.data["refresh"]

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
