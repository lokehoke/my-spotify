"""Админка как путь записи мимо UserManager.

Форма админки сохраняет объект напрямую, поэтому нормализацию email здесь
обеспечивает User.clean(), а профиль — UserAdmin.save_model.
"""

import pytest

from apps.users.models import User
from conftest import PASSWORD

pytestmark = pytest.mark.django_db

ADD_URL = "/admin/users/user/add/"
TOKEN_URL = "/api/v1/auth/token"

# ProfileInline рендерится и на странице создания: без management-формы инлайна
# формсет не проходит валидацию и POST возвращает форму вместо сохранения
INLINE = {
    "profile-TOTAL_FORMS": "0",
    "profile-INITIAL_FORMS": "0",
    "profile-MIN_NUM_FORMS": "0",
    "profile-MAX_NUM_FORMS": "1",
}


@pytest.fixture
def staff_client(client):
    admin = User.objects.create_superuser(email="admin@example.com", password=PASSWORD)
    client.force_login(admin)
    return client


def add_user(staff_client, email):
    return staff_client.post(
        ADD_URL,
        {"email": email, "password1": PASSWORD, "password2": PASSWORD, **INLINE},
    )


def test_admin_created_user_is_normalized_and_can_log_in(staff_client, api_client):
    response = add_user(staff_client, "Mixed.Case@Example.COM")
    assert response.status_code == 302, response.context["adminform"].form.errors

    user = User.objects.get(email="mixed.case@example.com")
    assert user.profile.display_name == "mixed.case"

    # Вход ищет пользователя по lowercase-адресу: до нормализации в clean()
    # заведённый в админке аккаунт здесь получал бы 401
    login = api_client.post(
        TOKEN_URL, {"email": "Mixed.Case@Example.COM", "password": PASSWORD}, format="json"
    )
    assert login.status_code == 200, login.data


def test_admin_rejects_email_differing_only_in_case(staff_client, user):
    """Дубль по регистру — ошибка валидации формы, а не IntegrityError."""
    response = add_user(staff_client, user.email.upper())
    assert response.status_code == 200  # форма переотрисована с ошибкой
    assert "email" in response.context["adminform"].form.errors
    assert User.objects.filter(email__iexact=user.email).count() == 1
