from django.db import OperationalError, connection
from django.http import JsonResponse
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


def healthz(request):
    """Liveness: константный ответ, без обращений к зависимостям."""
    return JsonResponse({"status": "ok"})


def readyz(request):
    """Readiness: проверяет PostgreSQL (позже — и Redis); сюда ходит балансировщик."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except OperationalError:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ready"})


class PingView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"pong": True})
