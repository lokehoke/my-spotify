"""Админка как путь записи мимо UserManager.

Форма сохраняет объект напрямую, поэтому нормализацию логина обеспечивает
User.normalize_username через AbstractBaseUser.clean(), а профиль —
UserAdmin.save_model.
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
    admin = User.objects.create_superuser(username="admin", password=PASSWORD)
    client.force_login(admin)
    return client


def add_user(staff_client, username):
    return staff_client.post(
        ADD_URL,
        {"username": username, "password1": PASSWORD, "password2": PASSWORD, **INLINE},
    )


def test_admin_created_user_is_normalized_and_can_log_in(staff_client, api_client):
    response = add_user(staff_client, "MixedCase")
    assert response.status_code == 302, response.context["adminform"].form.errors

    user = User.objects.get(username="mixedcase")
    assert user.profile.display_name == "mixedcase"

    # Вход ищет аккаунт по нормализованному логину: без normalize_username
    # заведённый в админке пользователь получал бы здесь 401
    login = api_client.post(
        TOKEN_URL, {"username": "MixedCase", "password": PASSWORD}, format="json"
    )
    assert login.status_code == 200, login.data


def test_admin_rejects_username_differing_only_in_case(staff_client, user):
    """Дубль по регистру — ошибка валидации формы, а не IntegrityError."""
    response = add_user(staff_client, user.username.upper())
    assert response.status_code == 200  # форма переотрисована с ошибкой
    assert "username" in response.context["adminform"].form.errors
    assert User.objects.filter(username__iexact=user.username).count() == 1
