import re

import pytest
from django.core import mail

pytestmark = pytest.mark.django_db

REQUEST_URL = "/api/v1/auth/email/verify/request"
CONFIRM_URL = "/api/v1/auth/email/verify/confirm"


def extract_token(message) -> str:
    match = re.search(r"token=([^\s]+)", message.body)
    assert match, message.body
    return match.group(1)


def test_registration_sends_verification_email(api_client):
    response = api_client.post(
        "/api/v1/auth/register",
        {"email": "verify@example.com", "password": "Sup3r-secret!42", "display_name": "V"},
        format="json",
    )
    assert response.status_code == 201
    assert len(mail.outbox) == 1
    assert "verify@example.com" in mail.outbox[0].to


def test_verify_flow(api_client, auth_client, user):
    mail.outbox.clear()
    request = auth_client.post(REQUEST_URL)
    assert request.status_code == 202
    token = extract_token(mail.outbox[0])

    confirm = api_client.post(CONFIRM_URL, {"token": token}, format="json")
    assert confirm.status_code == 204

    user.refresh_from_db()
    assert user.email_verified_at is not None
    assert auth_client.get("/api/v1/me").data["email_verified"] is True


def test_verify_request_requires_auth(api_client):
    assert api_client.post(REQUEST_URL).status_code == 401


def test_verify_confirm_rejects_garbage(api_client):
    response = api_client.post(CONFIRM_URL, {"token": "not-a-real-token"}, format="json")
    assert response.status_code == 400
    assert "token" in response.data["error"]["details"]


def test_verify_confirm_rejects_tampered_token(api_client, auth_client, user):
    mail.outbox.clear()
    auth_client.post(REQUEST_URL)
    token = extract_token(mail.outbox[0])

    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
    response = api_client.post(CONFIRM_URL, {"token": tampered}, format="json")
    assert response.status_code == 400
    user.refresh_from_db()
    assert user.email_verified_at is None


def test_verify_confirm_is_idempotent(api_client, auth_client):
    mail.outbox.clear()
    auth_client.post(REQUEST_URL)
    token = extract_token(mail.outbox[0])

    assert api_client.post(CONFIRM_URL, {"token": token}, format="json").status_code == 204
    assert api_client.post(CONFIRM_URL, {"token": token}, format="json").status_code == 204


def test_verify_request_skipped_when_already_verified(auth_client, user):
    from django.utils import timezone

    user.email_verified_at = timezone.now()
    user.save(update_fields=["email_verified_at"])

    mail.outbox.clear()
    assert auth_client.post(REQUEST_URL).status_code == 202
    assert mail.outbox == []
