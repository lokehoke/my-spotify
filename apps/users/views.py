from django.shortcuts import get_object_or_404
from rest_framework import generics, serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken, TokenError
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.users import selectors, services, tasks, tokens
from apps.users.models import Plan, User, UserDevice
from apps.users.serializers import (
    AccountDeleteSerializer,
    DeviceAwareTokenRefreshSerializer,
    DeviceSerializer,
    EmailTokenObtainPairSerializer,
    EmailVerifyConfirmSerializer,
    MeSerializer,
    PasswordChangeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    PlanSerializer,
    ProfileSerializer,
    RegisterSerializer,
    SubscriptionSerializer,
)


class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        tasks.send_email_verification.delay(user.id)
        return Response(
            {"public_id": str(user.public_id), "email": user.email},
            status=status.HTTP_201_CREATED,
        )


class TokenObtainView(TokenObtainPairView):
    serializer_class = EmailTokenObtainPairSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


class DeviceAwareTokenRefreshView(TokenRefreshView):
    serializer_class = DeviceAwareTokenRefreshSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


class LogoutView(APIView):
    def post(self, request):
        refresh = request.data.get("refresh")
        if not refresh:
            raise serializers.ValidationError({"refresh": ["Обязательное поле."]})
        try:
            RefreshToken(refresh).blacklist()
        except TokenError:
            pass  # уже отозван или истёк — logout идемпотентен
        return Response(status=status.HTTP_204_NO_CONTENT)


class LogoutAllView(APIView):
    """Выход на всех устройствах: все refresh-цепочки пользователя мертвы."""

    def post(self, request):
        services.logout_everywhere(user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class EmailVerifyRequestView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "email"

    def post(self, request):
        if request.user.email_verified_at is None:
            tasks.send_email_verification.delay(request.user.id)
        return Response(status=status.HTTP_202_ACCEPTED)


class EmailVerifyConfirmView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        serializer = EmailVerifyConfirmSerializer(data=request.data, context={})
        serializer.is_valid(raise_exception=True)
        services.confirm_email(user=serializer.context["target_user"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "email"

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(email=serializer.validated_data["email"], is_active=True).first()
        if user is not None:
            tasks.send_password_reset.delay(user.id)
        # Ответ одинаков независимо от существования аккаунта: иначе эндпоинт
        # превращается в проверялку «есть ли такой email в сервисе»
        return Response(status=status.HTTP_202_ACCEPTED)


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.change_password(
            user=serializer.validated_data["user"],
            new_password=serializer.validated_data["new_password"],
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    def get(self, request):
        return Response(MeSerializer(request.user).data)

    def patch(self, request):
        serializer = ProfileSerializer(request.user.profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        services.update_profile(profile=request.user.profile, **serializer.validated_data)
        return Response(MeSerializer(request.user).data)

    def delete(self, request):
        """Удаление аккаунта (152-ФЗ/GDPR): подтверждается паролем."""
        serializer = AccountDeleteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        services.delete_account(user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeExportView(APIView):
    """Выгрузка персональных данных одним JSON (право на переносимость)."""

    def get(self, request):
        user = request.user
        subscription = selectors.get_active_subscription(user)
        return Response(
            {
                "account": MeSerializer(user).data,
                "devices": DeviceSerializer(user.devices.all(), many=True).data,
                "subscriptions": SubscriptionSerializer(
                    user.subscriptions.select_related("plan").all(), many=True
                ).data,
                "active_subscription": (
                    SubscriptionSerializer(subscription).data if subscription else None
                ),
            }
        )


class PasswordChangeView(APIView):
    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        services.change_password(
            user=request.user, new_password=serializer.validated_data["new_password"]
        )
        # Смена пароля отзывает все refresh-цепочки — текущему клиенту сразу
        # выдаём новую пару, чтобы пользователя не выкидывало из приложения
        return Response(tokens.issue_tokens(request.user))


class DeviceListView(generics.ListAPIView):
    serializer_class = DeviceSerializer
    pagination_class = None  # устройств — единицы

    def get_queryset(self):
        return self.request.user.devices.order_by("-last_seen_at")


class DeviceRevokeView(APIView):
    def delete(self, request, pk: int):
        device = get_object_or_404(UserDevice, pk=pk, user=request.user)
        services.revoke_device(device=device)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PlanListView(generics.ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = PlanSerializer
    pagination_class = None
    queryset = Plan.objects.filter(is_active=True).order_by("price_cents")


class MySubscriptionView(APIView):
    def get(self, request):
        subscription = selectors.get_active_subscription(request.user)
        data = SubscriptionSerializer(subscription).data if subscription else None
        return Response({"subscription": data})
