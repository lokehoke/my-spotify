import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


class Quality(models.TextChoices):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, display_name="", **extra_fields):
        if not email:
            raise ValueError("Email обязателен")
        # Регистронезависимая уникальность: нормализация в lowercase
        # (вместо citext из DDL — рекомендация Django после депрекации CITextField)
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        # Профиль создаётся всегда, включая createsuperuser: код читает user.profile
        # без проверок, отсутствие строки давало бы 500
        UserProfile.objects.create(user=user, display_name=display_name or email.split("@")[0])
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """ARCHITECTURE.md §5.2: логин по email; наружу в API отдаётся только public_id."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    email = models.EmailField(unique=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    date_joined = models.DateTimeField(default=timezone.now)
    # Версия токенов: попадает claim'ом "tv" в refresh. Инкремент мгновенно убивает
    # все refresh-цепочки пользователя (смена/сброс пароля, logout-all, детект
    # переиспользования ротированного токена — ARCHITECTURE.md §7.4)
    token_version = models.PositiveIntegerField(default=0)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "users"

    def __str__(self):
        return self.email


class UserProfile(models.Model):
    """Профиль 1:1 — всё, что не касается аутентификации."""

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, primary_key=True, related_name="profile"
    )
    display_name = models.CharField(max_length=120)
    avatar_key = models.TextField(blank=True, default="")  # ключ в бакете images
    country = models.CharField(max_length=2, blank=True, default="")  # ISO 3166-1
    birth_date = models.DateField(null=True, blank=True)
    language = models.CharField(max_length=8, default="ru")
    preferred_quality = models.CharField(
        max_length=8, choices=Quality.choices, default=Quality.NORMAL
    )
    settings = models.JSONField(default=dict, blank=True)  # редкие флаги UI, не свалка
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_profiles"

    def __str__(self):
        return self.display_name


class Plan(models.Model):
    """Справочник тарифов: строк — единицы, правится админкой."""

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=120)
    price_cents = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=3, default="RUB")
    max_concurrent_streams = models.PositiveSmallIntegerField(default=1)
    max_offline_devices = models.PositiveSmallIntegerField(default=5)
    max_quality = models.CharField(max_length=8, choices=Quality.choices, default=Quality.NORMAL)
    trial_days = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "plans"

    def __str__(self):
        return self.code


class SubscriptionStatus(models.TextChoices):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    EXPIRED = "expired"


LIVE_SUBSCRIPTION_STATUSES = (
    SubscriptionStatus.TRIALING,
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.PAST_DUE,
)


class Subscription(TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="subscriptions")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=16, choices=SubscriptionStatus.choices)
    started_at = models.DateTimeField()
    current_period_end = models.DateTimeField()  # по нему решаем «премиум ли ещё»
    canceled_at = models.DateTimeField(null=True, blank=True)
    external_customer_id = models.CharField(max_length=255, blank=True, default="")
    external_sub_id = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "subscriptions"
        constraints = [
            # Не более одной «живой» подписки на пользователя
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status__in=LIVE_SUBSCRIPTION_STATUSES),
                name="uq_subscriptions_one_live",
            ),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.plan_id}:{self.status}"


class DeviceKind(models.TextChoices):
    WEB = "web"
    ANDROID = "android"
    IOS = "ios"


class UserDevice(models.Model):
    """Устройства: привязка refresh-токенов (claim device_id) и лимитов офлайна."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="devices")
    fingerprint = models.UUIDField()  # генерирует клиент при установке/первом входе
    kind = models.CharField(max_length=8, choices=DeviceKind.choices)
    name = models.CharField(max_length=120, blank=True, default="")
    app_version = models.CharField(max_length=32, blank=True, default="")
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    last_seen_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)  # «выйти на этом устройстве»

    class Meta:
        db_table = "user_devices"
        constraints = [
            models.UniqueConstraint(fields=["user", "fingerprint"], name="uq_user_devices_fp"),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.kind}:{self.name or self.fingerprint}"

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None
