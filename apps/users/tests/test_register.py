import pytest

from apps.users.models import User
from conftest import PASSWORD

pytestmark = pytest.mark.django_db

URL = "/api/v1/auth/register"


def test_register_success(api_client):
    response = api_client.post(
        URL,
        {"email": "new@example.com", "password": PASSWORD, "display_name": "Новый"},
        format="json",
    )
    assert response.status_code == 201
    assert response.data["email"] == "new@example.com"
    assert "public_id" in response.data
    assert "password" not in response.data

    user = User.objects.get(email="new@example.com")
    assert user.profile.display_name == "Новый"
    assert user.check_password(PASSWORD)


def test_register_email_normalized_to_lowercase(api_client):
    response = api_client.post(
        URL,
        {"email": "MiXeD@Example.COM", "password": PASSWORD, "display_name": "X"},
        format="json",
    )
    assert response.status_code == 201
    assert response.data["email"] == "mixed@example.com"

    # логин работает в любом регистре
    login = api_client.post(
        "/api/v1/auth/token",
        {"email": "MIXED@EXAMPLE.COM", "password": PASSWORD},
        format="json",
    )
    assert login.status_code == 200


def test_register_duplicate_email_case_insensitive(api_client, user):
    response = api_client.post(
        URL,
        {"email": user.email.upper(), "password": PASSWORD, "display_name": "Дубль"},
        format="json",
    )
    assert response.status_code == 400
    assert response.data["error"]["code"] == "validation_error"
    assert "email" in response.data["error"]["details"]


def test_register_weak_password_rejected(api_client):
    response = api_client.post(
        URL,
        {"email": "weak@example.com", "password": "12345678", "display_name": "X"},
        format="json",
    )
    assert response.status_code == 400
    assert "password" in response.data["error"]["details"]
    assert not User.objects.filter(email="weak@example.com").exists()
