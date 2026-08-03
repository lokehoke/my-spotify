from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Кастомная модель пользователя, заведена до первой миграции —
    поменять AUTH_USER_MODEL на живой базе практически невозможно.

    Поля из ARCHITECTURE.md §5.2 (public_id, email-логин, citext и т.д.)
    добавятся на этапе разработки приложения users.
    """
