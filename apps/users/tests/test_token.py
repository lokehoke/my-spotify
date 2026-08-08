import pytest

from conftest import PASSWORD

pytestmark = pytest.mark.django_db

TOKEN_URL = "/api/v1/auth/token"
REFRESH_URL = "/api/v1/auth/token/refresh"


def test_token_obtain_success(api_client, user):
    response = api_client.post(
        TOKEN_URL, {"email": user.email, "password": PASSWORD}, format="json"
    )
    assert response.status_code == 200
    assert response.data["access"]
    assert response.data["refresh"]
    assert response.data["access_expires_in"] == 900  # 15 минут (§7.4)


def test_token_obtain_wrong_password(api_client, user):
    response = api_client.post(
        TOKEN_URL, {"email": user.email, "password": "wrong-pass-1"}, format="json"
    )
    assert response.status_code == 401
    assert response.data["error"]["code"] == "no_active_account"


def test_token_obtain_inactive_user(api_client, user):
    user.is_active = False
    user.save(update_fields=["is_active"])
    response = api_client.post(
        TOKEN_URL, {"email": user.email, "password": PASSWORD}, format="json"
    )
    assert response.status_code == 401


def test_access_token_authenticates(auth_client):
    response = auth_client.get("/api/v1/me")
    assert response.status_code == 200


def test_garbage_access_token_rejected(api_client, user):
    api_client.credentials(HTTP_AUTHORIZATION="Bearer not-a-token")
    response = api_client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.data["error"]["code"] == "token_not_valid"


def test_refresh_rotates_and_blacklists_old(api_client, tokens):
    """§7.4: каждый refresh выдаёт новую пару, старый refresh мгновенно мёртв."""
    first = api_client.post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json")
    assert first.status_code == 200
    assert first.data["access"]
    assert first.data["refresh"] != tokens["refresh"]

    # новый refresh работает
    second = api_client.post(REFRESH_URL, {"refresh": first.data["refresh"]}, format="json")
    assert second.status_code == 200

    # повторное использование уже ротированного refresh — отказ
    # (последствия для всей цепочки проверяются в test_token_revocation.py)
    replay = api_client.post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json")
    assert replay.status_code == 401
    assert replay.data["error"]["code"] == "token_reuse_detected"


def test_logout_blacklists_refresh(api_client, auth_client, tokens):
    response = auth_client.post(
        "/api/v1/auth/logout", {"refresh": tokens["refresh"]}, format="json"
    )
    assert response.status_code == 204

    replay = api_client.post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json")
    assert replay.status_code == 401


def test_logout_requires_auth(api_client, tokens):
    response = api_client.post("/api/v1/auth/logout", {"refresh": tokens["refresh"]}, format="json")
    assert response.status_code == 401


def test_logout_requires_refresh_field(auth_client):
    response = auth_client.post("/api/v1/auth/logout", {}, format="json")
    assert response.status_code == 400


def test_auth_throttle_returns_429(api_client, user):
    """Scope auth: 10/min (§7.9) — 11-я попытка логина упирается в троттлинг."""
    for _ in range(10):
        api_client.post(TOKEN_URL, {"email": user.email, "password": "bad-pass"}, format="json")
    response = api_client.post(
        TOKEN_URL, {"email": user.email, "password": "bad-pass"}, format="json"
    )
    assert response.status_code == 429
    assert response.data["error"]["code"] == "throttled"
    assert "Retry-After" in response.headers
