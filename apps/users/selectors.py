"""Чтение данных users для вьюх и других приложений."""

from django.utils import timezone

from apps.users.models import LIVE_SUBSCRIPTION_STATUSES, Plan, Subscription, User


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
