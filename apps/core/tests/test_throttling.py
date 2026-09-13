"""Лимиты запросов: подделка заголовков и отказ кэша (§7.9, §5.8)."""

from unittest import mock

import pytest

pytestmark = pytest.mark.django_db

TOKEN_URL = "/api/v1/auth/token"


def attempt(api_client, **extra):
    return api_client.post(
        TOKEN_URL, {"username": "nobody", "password": "bad-pass"}, format="json", **extra
    )


def test_throttle_not_bypassed_by_forged_forwarded_for(api_client):
    """Без NUM_PROXIES DRF берёт X-Forwarded-For целиком, и каждый запрос с новым
    значением заголовка попадал бы в собственный бакет — брутфорс не ограничен."""
    for i in range(10):
        attempt(api_client, HTTP_X_FORWARDED_FOR=f"10.0.0.{i}, 203.0.113.7")

    response = attempt(api_client, HTTP_X_FORWARDED_FOR="10.0.0.250, 203.0.113.7")
    assert response.status_code == 429


def test_throttle_fails_open_when_cache_is_down(api_client, user):
    """Падение redis-cache даёт холодный кэш, а не 500 на каждый запрос (§5.8)."""
    from conftest import PASSWORD

    with mock.patch("django.core.cache.cache.get", side_effect=ConnectionError):
        response = api_client.post(
            TOKEN_URL, {"username": user.username, "password": PASSWORD}, format="json"
        )
    assert response.status_code == 200
