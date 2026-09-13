import pytest


@pytest.mark.django_db
def test_error_envelope_shape(api_client):
    """Ошибки — в едином конверте с code/message/details/request_id (§7.13)."""
    response = api_client.post("/api/v1/auth/register", {}, format="json")
    assert response.status_code == 400
    error = response.data["error"]
    assert error["code"] == "validation_error"
    assert error["message"]
    assert "username" in error["details"]
    assert error["request_id"]


@pytest.mark.django_db
def test_unauthenticated_error_envelope(api_client):
    response = api_client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.data["error"]["code"] == "not_authenticated"


@pytest.mark.django_db
def test_not_found_has_machine_readable_code(auth_client):
    """404 не должен отдавать code "error" и внутреннее сообщение ORM."""
    response = auth_client.delete("/api/v1/me/devices/999999")
    assert response.status_code == 404
    error = response.data["error"]
    assert error["code"] == "not_found"
    assert "UserDevice" not in error["message"]  # без деталей ORM наружу


@pytest.mark.django_db
def test_throttled_error_envelope(api_client):
    for _ in range(10):
        api_client.post(
            "/api/v1/auth/token",
            {"username": "nobody", "password": "bad"},
            format="json",
        )
    response = api_client.post(
        "/api/v1/auth/token", {"username": "nobody", "password": "bad"}, format="json"
    )
    assert response.status_code == 429
    assert response.data["error"]["code"] == "throttled"
    assert "Retry-After" in response.headers


@pytest.mark.django_db
def test_schema_is_public(api_client):
    response = api_client.get("/api/v1/schema/")
    assert response.status_code == 200
