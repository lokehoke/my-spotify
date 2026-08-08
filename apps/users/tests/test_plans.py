from datetime import timedelta

import pytest
from django.utils import timezone

from apps.users.models import Plan, Subscription, SubscriptionStatus

pytestmark = pytest.mark.django_db


def test_plans_are_seeded_and_public(api_client):
    response = api_client.get("/api/v1/plans")
    assert response.status_code == 200
    codes = {plan["code"] for plan in response.data}
    assert {"free", "premium"} <= codes

    free = next(p for p in response.data if p["code"] == "free")
    assert free["price_cents"] == 0
    assert free["max_quality"] == "normal"

    premium = next(p for p in response.data if p["code"] == "premium")
    assert premium["max_quality"] == "high"


def test_subscription_none_by_default(auth_client):
    response = auth_client.get("/api/v1/me/subscription")
    assert response.status_code == 200
    assert response.data["subscription"] is None


def test_active_subscription_changes_plan(auth_client, user):
    premium = Plan.objects.get(code="premium")
    Subscription.objects.create(
        user=user,
        plan=premium,
        status=SubscriptionStatus.ACTIVE,
        started_at=timezone.now(),
        current_period_end=timezone.now() + timedelta(days=30),
    )

    me = auth_client.get("/api/v1/me")
    assert me.data["plan"] == "premium"

    sub = auth_client.get("/api/v1/me/subscription")
    assert sub.data["subscription"]["status"] == "active"
    assert sub.data["subscription"]["plan"]["code"] == "premium"


def test_expired_subscription_falls_back_to_free(auth_client, user):
    premium = Plan.objects.get(code="premium")
    Subscription.objects.create(
        user=user,
        plan=premium,
        status=SubscriptionStatus.ACTIVE,
        started_at=timezone.now() - timedelta(days=60),
        current_period_end=timezone.now() - timedelta(days=1),  # период истёк
    )

    me = auth_client.get("/api/v1/me")
    assert me.data["plan"] == "free"
