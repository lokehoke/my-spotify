"""Чтение данных users для вьюх и других приложений.

Слои приложения — §7.1: views.py → services.py → selectors.py → models.py.
Запросы к моделям живут здесь, вьюхи их не строят.
"""

from django.db.models import QuerySet
from django.utils import timezone

from apps.users.models import LIVE_SUBSCRIPTION_STATUSES, Plan, Subscription, User, UserDevice


def get_active_subscription(user: User) -> Subscription | None:
    return (
        user.subscriptions.select_related("plan")
        .filter(
            status__in=LIVE_SUBSCRIPTION_STATUSES,
            current_period_end__gt=timezone.now(),
        )
        .order_by("-started_at")
        .first()
    )


def get_effective_plan(user: User) -> Plan:
    """Тариф пользователя; без живой подписки — free."""
    subscription = get_active_subscription(user)
    if subscription is not None:
        return subscription.plan
    return Plan.objects.get(code="free")


def list_subscriptions(user: User) -> QuerySet[Subscription]:
    return user.subscriptions.select_related("plan").order_by("-started_at")


def list_active_plans() -> QuerySet[Plan]:
    return Plan.objects.filter(is_active=True).order_by("price_cents")


def list_devices(user: User) -> QuerySet[UserDevice]:
    return user.devices.order_by("-last_seen_at")


def get_device(*, user: User, device_id) -> UserDevice | None:
    """Устройство в пределах аккаунта: чужое id вернёт None, а не чужую строку."""
    return user.devices.filter(id=device_id).first()
