from django.db import migrations


def seed_plans(apps, schema_editor):
    Plan = apps.get_model("users", "Plan")
    Plan.objects.update_or_create(
        code="free",
        defaults={
            "name": "Бесплатный",
            "price_cents": 0,
            "currency": "RUB",
            "max_concurrent_streams": 1,
            "max_offline_devices": 0,
            "max_quality": "normal",
            "trial_days": 0,
            "is_active": True,
        },
    )
    Plan.objects.update_or_create(
        code="premium",
        defaults={
            "name": "Премиум",
            "price_cents": 19900,
            "currency": "RUB",
            "max_concurrent_streams": 1,
            "max_offline_devices": 5,
            "max_quality": "high",
            "trial_days": 30,
            "is_active": True,
        },
    )


def unseed_plans(apps, schema_editor):
    Plan = apps.get_model("users", "Plan")
    Plan.objects.filter(code__in=["free", "premium"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_plans, unseed_plans),
    ]
