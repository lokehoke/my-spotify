from django.core.cache import cache
from django.db import OperationalError, connection
from django.http import JsonResponse
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


def healthz(request):
    """Liveness: константный ответ, без обращений к зависимостям.

    Используется только рестарт-политикой оркестратора. Балансировщик и внешний
    uptime-мониторинг ходят в /readyz (ARCHITECTURE.md §7.13).
    """
    return JsonResponse({"status": "ok"})


def readyz(request):
    """Readiness: PostgreSQL + Redis-кэш. Отказ = ноду надо вывести из ротации."""
    checks = {}

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = "ok"
    except OperationalError:
        checks["database"] = "unavailable"

    try:
        cache.set("readyz", "1", timeout=5)
        checks["cache"] = "ok" if cache.get("readyz") == "1" else "unavailable"
    except Exception:
        checks["cache"] = "unavailable"

    healthy = all(value == "ok" for value in checks.values())
    return JsonResponse(
        {"status": "ready" if healthy else "unavailable", "checks": checks},
        status=200 if healthy else 503,
    )


class PingView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"pong": True})
