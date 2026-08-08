import pytest

from conftest import PASSWORD

pytestmark = pytest.mark.django_db

ME_URL = "/api/v1/me"


def test_me_requires_auth(api_client):
    assert api_client.get(ME_URL).status_code == 401


def test_me_returns_profile_and_plan(auth_client, user):
    response = auth_client.get(ME_URL)
    assert response.status_code == 200
    data = response.data
    assert data["email"] == user.email
    assert data["public_id"] == str(user.public_id)
    assert data["email_verified"] is False
    assert data["profile"]["display_name"] == "Тестовый Юзер"
    assert data["profile"]["preferred_quality"] == "normal"
    assert data["plan"] == "free"
    assert "id" not in data  # наружу только public_id (§5.1)


def test_me_patch_updates_profile(auth_client):
    response = auth_client.patch(
        ME_URL,
        {"display_name": "Новое Имя", "preferred_quality": "high", "language": "en"},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["profile"]["display_name"] == "Новое Имя"
    assert response.data["profile"]["preferred_quality"] == "high"
    assert response.data["profile"]["language"] == "en"


def test_me_patch_invalid_quality(auth_client):
    response = auth_client.patch(ME_URL, {"preferred_quality": "ultra"}, format="json")
    assert response.status_code == 400
    assert "preferred_quality" in response.data["error"]["details"]


def test_me_patch_settings_accepts_flat_object(auth_client):
    response = auth_client.patch(ME_URL, {"settings": {"theme": "dark"}}, format="json")
    assert response.status_code == 200
    assert response.data["profile"]["settings"] == {"theme": "dark"}


def test_me_patch_settings_rejects_non_object(auth_client):
    response = auth_client.patch(ME_URL, {"settings": ["dark"]}, format="json")
    assert response.status_code == 400
    assert "settings" in response.data["error"]["details"]


def test_me_patch_settings_rejects_oversized_payload(auth_client):
    """settings — флаги UI, а не бесплатное хранилище на пользователя."""
    response = auth_client.patch(ME_URL, {"settings": {"blob": "x" * 5000}}, format="json")
    assert response.status_code == 400
    assert "settings" in response.data["error"]["details"]


def test_me_patch_cannot_change_email(auth_client, user):
    response = auth_client.patch(ME_URL, {"email": "hax@example.com"}, format="json")
    assert response.status_code == 200  # неизвестные поля игнорируются
    user.refresh_from_db()
    assert user.email != "hax@example.com"


def test_password_change_wrong_current(auth_client):
    response = auth_client.post(
        "/api/v1/me/password",
        {"current_password": "wrong", "new_password": "N3w-Sup3r-pass!"},
        format="json",
    )
    assert response.status_code == 400
    assert "current_password" in response.data["error"]["details"]


def test_password_change_success(api_client, auth_client, user):
    response = auth_client.post(
        "/api/v1/me/password",
        {"current_password": PASSWORD, "new_password": "N3w-Sup3r-pass!"},
        format="json",
    )
    assert response.status_code == 200  # взамен отозванных выдаётся новая пара токенов

    old_login = api_client.post(
        "/api/v1/auth/token", {"email": user.email, "password": PASSWORD}, format="json"
    )
    assert old_login.status_code == 401

    new_login = api_client.post(
        "/api/v1/auth/token",
        {"email": user.email, "password": "N3w-Sup3r-pass!"},
        format="json",
    )
    assert new_login.status_code == 200
