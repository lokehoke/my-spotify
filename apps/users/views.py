import logging

from kombu.exceptions import OperationalError as BrokerUnavailable
from rest_framework import exceptions, generics, serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken, TokenError
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.core.throttling import ScopedRateThrottle
from apps.users import selectors, services, tasks, tokens
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

logger = logging.getLogger(__name__)


def enqueue_email(task, *args) -> None:
    """Письмо — не критичный путь: недоступность брокера не должна валить
    регистрацию или запрос сброса пароля 500-й ошибкой."""
    try:
        task.delay(*args)
    except BrokerUnavailable:
        logger.exception("Не удалось поставить письмо в очередь: %s", task.name)


class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user = serializer.save()
        except services.EmailAlreadyTaken:
            raise serializers.ValidationError(
                {"email": ["Пользователь с таким email уже существует."]}
            ) from None
        enqueue_email(tasks.send_email_verification, user.id)
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


class EmailVerifyRequestView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "email"

    def post(self, request):
        if request.user.email_verified_at is None:
            enqueue_email(tasks.send_email_verification, request.user.id)
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
        user = selectors.get_active_user_by_email(serializer.validated_data["email"])
        if user is not None:
            enqueue_email(tasks.send_password_reset, user.id)
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
