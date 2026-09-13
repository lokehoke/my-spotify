import pytest

from apps.users.models import User
from conftest import PASSWORD

pytestmark = pytest.mark.django_db

REGISTER_URL = "/api/v1/auth/register"
TOKEN_URL = "/api/v1/auth/token"


def test_register_success(api_client):
    response = api_client.post(
        REGISTER_URL, {"username": "newbie", "password": PASSWORD}, format="json"
    )
    assert response.status_code == 201
    assert response.data["username"] == "newbie"
    assert "public_id" in response.data
    assert "password" not in response.data

    user = User.objects.get(username="newbie")
    # Отображаемое имя по умолчанию — сам логин: регистрация его больше не спрашивает
    assert user.profile.display_name == "newbie"
    assert user.check_password(PASSWORD)


def test_register_username_normalized_to_lowercase(api_client):
    response = api_client.post(
        REGISTER_URL, {"username": "MiXeD", "password": PASSWORD}, format="json"
    )
    assert response.status_code == 201
    assert response.data["username"] == "mixed"

    login = api_client.post(TOKEN_URL, {"username": "MIXED", "password": PASSWORD}, format="json")
    assert login.status_code == 200


def test_register_duplicate_username_case_insensitive(api_client, user):
    response = api_client.post(
        REGISTER_URL, {"username": user.username.upper(), "password": PASSWORD}, format="json"
    )
    assert response.status_code == 400
    assert "username" in response.data["error"]["details"]
    assert User.objects.filter(username__iexact=user.username).count() == 1


def test_register_weak_password_rejected(api_client):
    response = api_client.post(
        REGISTER_URL, {"username": "weakling", "password": "12345678"}, format="json"
    )
    assert response.status_code == 400
    assert "password" in response.data["error"]["details"]
    assert not User.objects.filter(username="weakling").exists()


@pytest.mark.parametrize(
    "username",
    [
        "ab",  # короче трёх символов
        "_leading",  # начинается не с буквы и не с цифры
        "with space",
        "имя",  # кириллица не допускается, см. username_validator
        "a" * 33,  # длиннее 32
    ],
)
def test_register_rejects_invalid_username(api_client, username):
    response = api_client.post(
        REGISTER_URL, {"username": username, "password": PASSWORD}, format="json"
    )
    assert response.status_code == 400
    assert "username" in response.data["error"]["details"]
