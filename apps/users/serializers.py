import json
import uuid

from django.contrib.auth.password_validation import validate_password
from rest_framework import exceptions, serializers
from rest_framework.throttling import BaseThrottle
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from apps.users import services, tokens
from apps.users.models import (
    ChainRevocationReason,
    DeviceKind,
    Plan,
    Subscription,
    User,
    UserDevice,
    UserProfile,
    username_validator,
)

SETTINGS_MAX_BYTES = 4096


class RegisterSerializer(serializers.Serializer):
    """Регистрация — только логин и пароль: почтового контура в MVP нет."""

    username = serializers.CharField(max_length=32, validators=[username_validator])
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_username(self, value: str) -> str:
        value = User.normalize_username(value)
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("Пользователь с таким логином уже существует.")
        return value

    def validate_password(self, value: str) -> str:
        validate_password(value)
        return value

    def create(self, validated_data):
        return services.register_user(**validated_data)


class DeviceInputSerializer(serializers.Serializer):
    fingerprint = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=DeviceKind.choices)
    name = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    app_version = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")


class UsernameTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Вход по логину; опциональный блок device регистрирует устройство и вшивает
    device_id в refresh (ARCHITECTURE.md §5.7: отзыв устройства инвалидирует его токен).
    """

    device = DeviceInputSerializer(required=False, write_only=True)

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token[tokens.TOKEN_VERSION_CLAIM] = user.token_version
        token[tokens.CHAIN_CLAIM] = uuid.uuid4().hex
        return token

    def validate(self, attrs):
        # Ровно та же нормализация, что и при записи: иначе вход логином в другом
        # регистре не нашёл бы аккаунт
        attrs[self.username_field] = User.normalize_username(attrs[self.username_field])
        device_data = attrs.pop("device", None)
        data = super().validate(attrs)
        data["access_expires_in"] = tokens.access_lifetime_seconds()

        if device_data:
            request = self.context.get("request")
            # Не REMOTE_ADDR: за nginx это всегда адрес прокси, и поле «откуда
            # заходили с устройства» стало бы бесполезным при разборе инцидента.
            # get_ident учитывает NUM_PROXIES, поэтому заголовок не подделать.
            ip = BaseThrottle().get_ident(request) if request else None
            device = services.register_device(user=self.user, ip=ip, **device_data)
            # jti сохраняется: OutstandingToken, созданный в get_token, остаётся валидным
            refresh = RefreshToken(data["refresh"])
            refresh[tokens.DEVICE_CLAIM] = device.id
            data["refresh"] = str(refresh)
            data["access"] = str(refresh.access_token)
        return data


class DeviceAwareTokenRefreshSerializer(TokenRefreshSerializer):
    """Ротация refresh с проверками поверх стандартной (§7.4).

    Порядок проверок важен и разделяет три разных ситуации, которые внешне
    выглядят одинаково: токен отозван явным действием владельца, цепочка
    завершена выходом, токен предъявлен повторно.
    """

    def validate(self, attrs):
        payload = tokens.decode_refresh_payload(attrs["refresh"])
        if payload is None:
            return super().validate(attrs)  # мусорный/протухший — стандартный invalid_token

        user = User.objects.filter(pk=payload.get("user_id")).first()

        # 1. Версия токенов: смена пароля и «выйти везде» гасят всё разом.
        if user is None or payload.get(tokens.TOKEN_VERSION_CLAIM) != user.token_version:
            raise exceptions.AuthenticationFailed("Токен отозван.", code="token_revoked")

        # 2. Цепочка завершена (выход на устройстве или ранее пойманный реюз).
        chain_id = payload.get(tokens.CHAIN_CLAIM)
        if tokens.is_chain_revoked(chain_id):
            raise exceptions.AuthenticationFailed("Токен отозван.", code="token_revoked")

        # 3. Токен уже ротирован. Свежий повтор — это гонка клиента (ретрай,
        # параллельные запросы при возврате из фона), и отзывать за неё нельзя.
        # Старый повтор означает, что копия токена есть у кого-то ещё: гасим
        # ровно эту цепочку, остальные устройства пользователя не трогаем.
        entry = tokens.get_blacklist_entry(payload)
        if entry is not None:
            if tokens.is_recent_blacklist(entry):
                raise exceptions.AuthenticationFailed(
                    "Токен уже был обновлён.", code="token_not_valid"
                )
            services.revoke_token_chain(
                user=user, chain_id=chain_id, reason=ChainRevocationReason.REUSE
            )
            raise exceptions.AuthenticationFailed(
                "Обнаружено повторное использование refresh-токена, сессия завершена.",
                code="token_reuse_detected",
            )

        device_id = payload.get(tokens.DEVICE_CLAIM)
        if device_id is not None:
            device = UserDevice.objects.filter(id=device_id).first()
            if device is None or device.is_revoked:
                raise exceptions.AuthenticationFailed("Устройство отозвано.", code="device_revoked")

        data = super().validate(attrs)
        if device_id is not None:
            services.touch_device(device_id=device_id)
        return data


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = [
            "display_name",
            "avatar_key",
            "country",
            "birth_date",
            "language",
            "preferred_quality",
            "settings",
        ]
        read_only_fields = ["avatar_key"]  # задаётся через presigned upload (этап медиа)

    def validate_settings(self, value):
        """settings — плоские флаги UI, а не свалка: объект и лимит размера."""
        if not isinstance(value, dict):
            raise serializers.ValidationError("Ожидается объект.")
        if len(json.dumps(value)) > SETTINGS_MAX_BYTES:
            raise serializers.ValidationError(
                f"Слишком большой объект (лимит {SETTINGS_MAX_BYTES} байт)."
            )
        return value


class MeSerializer(serializers.ModelSerializer):
    profile = ProfileSerializer()
    plan = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["public_id", "username", "date_joined", "profile", "plan"]

    def get_plan(self, obj) -> str:
        from apps.users import selectors

        return selectors.get_effective_plan(obj).code


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_current_password(self, value: str) -> str:
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("Неверный текущий пароль.")
        return value

    def validate_new_password(self, value: str) -> str:
        validate_password(value, user=self.context["request"].user)
        return value


class AccountDeleteSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_password(self, value: str) -> str:
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("Неверный пароль.")
        return value


class DeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserDevice
        fields = [
            "id",
            "fingerprint",
            "kind",
            "name",
            "app_version",
            "last_seen_at",
            "created_at",
            "revoked_at",
        ]


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = [
            "code",
            "name",
            "price_cents",
            "currency",
            "max_quality",
            "max_concurrent_streams",
            "max_offline_devices",
            "trial_days",
        ]


class SubscriptionSerializer(serializers.ModelSerializer):
    plan = PlanSerializer()

    class Meta:
        model = Subscription
        fields = ["status", "plan", "started_at", "current_period_end", "canceled_at"]
