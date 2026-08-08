from unittest import mock

import pytest
from django.db import OperationalError


def test_healthz_is_constant(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_healthz_stays_ok_when_database_is_down(client):
    """Liveness не должен зависеть от зависимостей: иначе оркестратор будет
    перезапускать здоровый процесс из-за недоступной БД."""
    with mock.patch("django.db.backends.utils.CursorWrapper.execute", side_effect=OperationalError):
        response = client.get("/healthz")
    assert response.status_code == 200


@pytest.mark.django_db
def test_readyz_ok(client):
    response = client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"database": "ok", "cache": "ok"}


@pytest.mark.django_db
def test_readyz_503_when_database_is_down(client):
    """Проверка действительно ходит в БД, а не возвращает константу."""
    with mock.patch("django.db.backends.utils.CursorWrapper.execute", side_effect=OperationalError):
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "unavailable"


@pytest.mark.django_db
def test_readyz_503_when_cache_is_down(client):
    with mock.patch("django.core.cache.cache.set", side_effect=ConnectionError):
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["cache"] == "unavailable"


def test_ping_is_public(client):
    response = client.get("/api/v1/ping")
    assert response.status_code == 200
    assert response.json() == {"pong": True}


def test_request_id_generated(client):
    response = client.get("/healthz")
    assert response.headers["X-Request-ID"]


def test_request_id_passthrough(client):
    response = client.get("/healthz", headers={"X-Request-ID": "req-abc-123"})
    assert response.headers["X-Request-ID"] == "req-abc-123"


def test_request_id_is_length_capped(client):
    """Внешний заголовок не доверенный: длина обрезается, чтобы не раздувать логи."""
    response = client.get("/healthz", headers={"X-Request-ID": "z" * 500})
    assert len(response.headers["X-Request-ID"]) == 64
