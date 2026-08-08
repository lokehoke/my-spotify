import uuid

import pytest

from apps.users.models import UserDevice
from conftest import PASSWORD

pytestmark = pytest.mark.django_db

TOKEN_URL = "/api/v1/auth/token"
REFRESH_URL = "/api/v1/auth/token/refresh"

DEVICE = {
    "fingerprint": "3f1c9a1e-6a1b-4f0e-9d2c-8b7a5c4d3e2f",
    "kind": "android",
    "name": "Pixel 9",
    "app_version": "1.0.0",
}


def login_with_device(api_client, user, device=DEVICE):
    return api_client.post(
        TOKEN_URL,
        {"email": user.email, "password": PASSWORD, "device": device},
        format="json",
    )


def test_login_with_device_registers_it(api_client, user):
    response = login_with_device(api_client, user)
    assert response.status_code == 200

    device = UserDevice.objects.get(user=user)
    assert str(device.fingerprint) == DEVICE["fingerprint"]
    assert device.kind == "android"
    assert device.name == "Pixel 9"
    assert device.revoked_at is None


def test_repeat_login_same_fingerprint_does_not_duplicate(api_client, user):
    login_with_device(api_client, user)
    login_with_device(api_client, user, {**DEVICE, "name": "Pixel 9 Pro"})

    devices = UserDevice.objects.filter(user=user)
    assert devices.count() == 1
    assert devices.get().name == "Pixel 9 Pro"


def test_device_bound_refresh_works(api_client, user):
    tokens = login_with_device(api_client, user).data
    response = api_client.post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json")
    assert response.status_code == 200


def test_devices_list(api_client, user):
    tokens = login_with_device(api_client, user).data
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    response = api_client.get("/api/v1/me/devices")
    assert response.status_code == 200
    assert len(response.data) == 1
    assert response.data[0]["kind"] == "android"


def test_revoked_device_refresh_rejected(api_client, user):
    """§5.7: отзыв устройства инвалидирует его refresh-токен."""
    tokens = login_with_device(api_client, user).data
    device = UserDevice.objects.get(user=user)

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    revoke = api_client.delete(f"/api/v1/me/devices/{device.id}")
    assert revoke.status_code == 204

    api_client.credentials()
    response = api_client.post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json")
    assert response.status_code == 401
    assert response.data["error"]["code"] == "device_revoked"


def test_revoke_is_idempotent(api_client, user):
    tokens = login_with_device(api_client, user).data
    device = UserDevice.objects.get(user=user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    assert api_client.delete(f"/api/v1/me/devices/{device.id}").status_code == 204
    assert api_client.delete(f"/api/v1/me/devices/{device.id}").status_code == 204


def test_cannot_revoke_foreign_device(auth_client, create_user):
    other = create_user(email="other@example.com")
    foreign = UserDevice.objects.create(
        user=other, fingerprint=uuid.uuid4(), kind="web", name="Chrome"
    )
    response = auth_client.delete(f"/api/v1/me/devices/{foreign.id}")
    assert response.status_code == 404
    foreign.refresh_from_db()
    assert foreign.revoked_at is None


def test_relogin_unrevokes_device(api_client, user):
    tokens = login_with_device(api_client, user).data
    device = UserDevice.objects.get(user=user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    api_client.delete(f"/api/v1/me/devices/{device.id}")
    api_client.credentials()

    # новый вход по паролю на том же устройстве снимает отзыв
    response = login_with_device(api_client, user)
    assert response.status_code == 200
    device.refresh_from_db()
    assert device.revoked_at is None

    refresh = api_client.post(REFRESH_URL, {"refresh": response.data["refresh"]}, format="json")
    assert refresh.status_code == 200
