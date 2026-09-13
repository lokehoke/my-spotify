from rest_framework import exceptions, generics, serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken, TokenError
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.core.throttling import ScopedRateThrottle
from apps.users import selectors, services, tokens
from apps.users.serializers import (
    AccountDeleteSerializer,
    DeviceAwareTokenRefreshSerializer,
    DeviceSerializer,
    MeSerializer,
    PasswordChangeSerializer,
    PlanSerializer,
    ProfileSerializer,
    RegisterSerializer,
    SubscriptionSerializer,
    UsernameTokenObtainPairSerializer,
)


class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user = serializer.save()
        except services.UsernameAlreadyTaken:
            raise serializers.ValidationError(
                {"username": ["Пользователь с таким логином уже существует."]}
            ) from None
        return Response(
            {"public_id": str(user.public_id), "username": user.username},
            status=status.HTTP_201_CREATED,
        )


class TokenObtainView(TokenObtainPairView):
    serializer_class = UsernameTokenObtainPairSerializer
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

        # Цепочка помечается завершённой явно: иначе повторный запрос с тем же
        # токеном (in-flight, ретрай) выглядел бы как кража чужого токена
        payload = tokens.decode_refresh_payload(refresh)
        if payload is not None and payload.get("user_id") == str(request.user.id):
            services.logout_chain(user=request.user, chain_id=payload.get(tokens.CHAIN_CLAIM))
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
                "devices": DeviceSerializer(selectors.list_devices(user), many=True).data,
                "subscriptions": SubscriptionSerializer(
                    selectors.list_subscriptions(user), many=True
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
        # Привязка к устройству берётся из текущего access-токена: иначе новая
        # пара теряет device_id и сессию больше нельзя отозвать через /me/devices
        device = None
        device_id = (request.auth.payload if request.auth else {}).get(tokens.DEVICE_CLAIM)
        if device_id is not None:
            device = selectors.get_device(user=request.user, device_id=device_id)

        services.change_password(
            user=request.user, new_password=serializer.validated_data["new_password"]
        )
        # Смена пароля отзывает все refresh-цепочки — текущему клиенту сразу
        # выдаём новую пару, чтобы пользователя не выкидывало из приложения
        return Response(tokens.issue_tokens(request.user, device=device))


class DeviceListView(generics.ListAPIView):
    serializer_class = DeviceSerializer
    pagination_class = None  # устройств — единицы

    def get_queryset(self):
        return selectors.list_devices(self.request.user)


class DeviceRevokeView(APIView):
    def delete(self, request, pk: int):
        device = selectors.get_device(user=request.user, device_id=pk)
        if device is None:
            raise exceptions.NotFound()
        services.revoke_device(device=device)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PlanListView(generics.ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = PlanSerializer
    pagination_class = None

    def get_queryset(self):
        return selectors.list_active_plans()


class MySubscriptionView(APIView):
    def get(self, request):
        subscription = selectors.get_active_subscription(request.user)
        data = SubscriptionSerializer(subscription).data if subscription else None
        return Response({"subscription": data})
