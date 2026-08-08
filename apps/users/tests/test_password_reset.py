import re

import pytest
from django.core import mail

from conftest import PASSWORD

pytestmark = pytest.mark.django_db

REQUEST_URL = "/api/v1/auth/password/reset"
CONFIRM_URL = "/api/v1/auth/password/reset/confirm"
TOKEN_URL = "/api/v1/auth/token"


def extract_reset_params(message) -> dict:
    match = re.search(r"uid=([^&\s]+)&token=([^\s]+)", message.body)
    assert match, message.body
    return {"uid": match.group(1), "token": match.group(2)}


def test_reset_request_sends_email(api_client, user):
    response = api_client.post(REQUEST_URL, {"email": user.email}, format="json")
    assert response.status_code == 202
    assert len(mail.outbox) == 1
    assert user.email in mail.outbox[0].to


def test_reset_request_unknown_email_looks_identical(api_client, user):
    """Ответ не должен выдавать, зарегистрирован ли email (user enumeration)."""
    known = api_client.post(REQUEST_URL, {"email": user.email}, format="json")
    mail.outbox.clear()
    unknown = api_client.post(REQUEST_URL, {"email": "nobody@example.com"}, format="json")

    assert known.status_code == unknown.status_code == 202
    assert known.data == unknown.data
    assert mail.outbox == []  # письмо несуществующему адресату не уходит


def test_reset_request_case_insensitive(api_client, user):
    response = api_client.post(REQUEST_URL, {"email": user.email.upper()}, format="json")
    assert response.status_code == 202
    assert len(mail.outbox) == 1


def test_reset_confirm_sets_new_password(api_client, user):
    api_client.post(REQUEST_URL, {"email": user.email}, format="json")
    params = extract_reset_params(mail.outbox[0])

    response = api_client.post(
        CONFIRM_URL, {**params, "new_password": "Br4nd-new-pass!"}, format="json"
    )
    assert response.status_code == 204

    assert (
        api_client.post(
            TOKEN_URL, {"email": user.email, "password": PASSWORD}, format="json"
        ).status_code
        == 401
    )
    assert (
        api_client.post(
            TOKEN_URL, {"email": user.email, "password": "Br4nd-new-pass!"}, format="json"
        ).status_code
        == 200
    )


def test_reset_token_is_single_use(api_client, user):
    api_client.post(REQUEST_URL, {"email": user.email}, format="json")
    params = extract_reset_params(mail.outbox[0])

    first = api_client.post(
        CONFIRM_URL, {**params, "new_password": "Br4nd-new-pass!"}, format="json"
    )
    assert first.status_code == 204

    replay = api_client.post(
        CONFIRM_URL, {**params, "new_password": "An0ther-pass!42"}, format="json"
    )
    assert replay.status_code == 400
    assert "token" in replay.data["error"]["details"]


def test_reset_confirm_rejects_bad_token(api_client, user):
    api_client.post(REQUEST_URL, {"email": user.email}, format="json")
    params = extract_reset_params(mail.outbox[0])

    response = api_client.post(
        CONFIRM_URL,
        {**params, "token": "invalid-token", "new_password": "Br4nd-new!1"},
        format="json",
    )
    assert response.status_code == 400
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


def test_reset_confirm_rejects_weak_password(api_client, user):
    api_client.post(REQUEST_URL, {"email": user.email}, format="json")
    params = extract_reset_params(mail.outbox[0])

    response = api_client.post(CONFIRM_URL, {**params, "new_password": "12345678"}, format="json")
    assert response.status_code == 400
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


def test_reset_kills_existing_sessions(api_client, user, tokens):
    """Сброс пароля обязан обесточить украденную сессию."""
    api_client.post(REQUEST_URL, {"email": user.email}, format="json")
    params = extract_reset_params(mail.outbox[0])
    api_client.post(CONFIRM_URL, {**params, "new_password": "Br4nd-new-pass!"}, format="json")

    response = api_client.post(
        "/api/v1/auth/token/refresh", {"refresh": tokens["refresh"]}, format="json"
    )
    assert response.status_code == 401
    assert response.data["error"]["code"] == "token_revoked"
