import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

PASSWORD = "Sup3r-secret!42"


@pytest.fixture(autouse=True)
def _clear_cache():
    """Троттлинг DRF копит счётчики в кэше — изолируем тесты друг от друга."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def create_user(db):
    def _make(email="user@example.com", password=PASSWORD, display_name="Тестовый Юзер"):
        from apps.users.services import register_user

        return register_user(email=email, password=password, display_name=display_name)

    return _make


@pytest.fixture
def user(create_user):
    return create_user()


@pytest.fixture
def tokens(api_client, user):
    response = api_client.post(
        "/api/v1/auth/token",
        {"email": user.email, "password": PASSWORD},
        format="json",
    )
    assert response.status_code == 200, response.data
    return response.data


@pytest.fixture
def auth_client(api_client, tokens):
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    return api_client
