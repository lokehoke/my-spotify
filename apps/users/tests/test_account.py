"""Удаление аккаунта и экспорт данных (152-ФЗ/GDPR) + профиль без 500."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.users.models import Plan, Subscription, SubscriptionStatus, User, UserDevice
from conftest import PASSWORD

pytestmark = pytest.mark.django_db

ME_URL = "/api/v1/me"


def test_delete_requires_correct_password(auth_client, user):
    response = auth_client.delete(ME_URL, {"password": "wrong-pass"}, format="json")
    assert response.status_code == 400
    assert User.objects.filter(pk=user.pk).exists()


def test_delete_requires_password_field(auth_client, user):
    response = auth_client.delete(ME_URL, {}, format="json")
    assert response.status_code == 400
    assert User.objects.filter(pk=user.pk).exists()


def test_delete_removes_account_and_related_data(auth_client, user):
    UserDevice.objects.create(
        user=user, fingerprint="7c9e6679-7425-40de-944b-e07fc1f90ae7", kind="web"
    )
    Subscription.objects.create(
        user=user,
        plan=Plan.objects.get(code="premium"),
        status=SubscriptionStatus.ACTIVE,
        started_at=timezone.now(),
        current_period_end=timezone.now() + timedelta(days=30),
    )

    response = auth_client.delete(ME_URL, {"password": PASSWORD}, format="json")
    assert response.status_code == 204

    assert not User.objects.filter(pk=user.pk).exists()
    assert not UserDevice.objects.filter(user_id=user.pk).exists()
    assert not Subscription.objects.filter(user_id=user.pk).exists()


def test_delete_requires_auth(api_client):
    assert api_client.delete(ME_URL, {"password": PASSWORD}, format="json").status_code == 401


def test_export_returns_personal_data(auth_client, user):
    UserDevice.objects.create(
        user=user, fingerprint="7c9e6679-7425-40de-944b-e07fc1f90ae7", kind="ios", name="iPhone"
    )
    response = auth_client.get("/api/v1/me/export")
    assert response.status_code == 200
    assert response.data["account"]["email"] == user.email
    assert len(response.data["devices"]) == 1
    assert response.data["devices"][0]["name"] == "iPhone"
    assert response.data["subscriptions"] == []
    assert response.data["active_subscription"] is None


def test_export_requires_auth(api_client):
    assert api_client.get("/api/v1/me/export").status_code == 401


def test_export_does_not_leak_other_users(auth_client, create_user):
    other = create_user(email="other@example.com")
    UserDevice.objects.create(
        user=other, fingerprint="11111111-1111-1111-1111-111111111111", kind="web"
    )
    response = auth_client.get("/api/v1/me/export")
    assert response.data["devices"] == []


def test_superuser_has_profile(db):
    """createsuperuser не проходит через register_user — профиль всё равно нужен,
    иначе GET /me падает 500."""
    admin = User.objects.create_superuser(email="admin@example.com", password=PASSWORD)
    assert admin.profile.display_name == "admin"


def test_me_works_for_superuser(api_client, db):
    User.objects.create_superuser(email="admin@example.com", password=PASSWORD)
    tokens = api_client.post(
        "/api/v1/auth/token", {"email": "admin@example.com", "password": PASSWORD}, format="json"
    ).data
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    response = api_client.get(ME_URL)
    assert response.status_code == 200
    assert response.data["profile"]["display_name"] == "admin"
