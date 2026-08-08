import json

from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from rest_framework import exceptions, serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from apps.users import services, tokens
from apps.users.models import DeviceKind, Plan, Subscription, User, UserDevice, UserProfile

SETTINGS_MAX_BYTES = 4096


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    display_name = serializers.CharField(max_length=120)

    def validate_email(self, value: str) -> str:
        value = value.lower()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("Пользователь с таким email уже существует.")
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


class EmailTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Логин по email; опциональный блок device регистрирует устройство и вшивает
    device_id в refresh (ARCHITECTURE.md §5.7: отзыв устройства инвалидирует его токен).
    """

    device = DeviceInputSerializer(required=False, write_only=True)

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token[tokens.TOKEN_VERSION_CLAIM] = user.token_version
        return token

    def validate(self, attrs):
        attrs[self.username_field] = attrs[self.username_field].lower()
        device_data = attrs.pop("device", None)
        data = super().validate(attrs)
        data["access_expires_in"] = tokens.access_lifetime_seconds()

        if device_data:
            request = self.context.get("request")
            ip = request.META.get("REMOTE_ADDR") if request else None
            device = services.register_device(user=self.user, ip=ip, **device_data)
            # jti сохраняется: OutstandingToken, созданный в get_token, остаётся валидным
            refresh = RefreshToken(data["refresh"])
            refresh[tokens.DEVICE_CLAIM] = device.id
            data["refresh"] = str(refresh)
            data["access"] = str(refresh.access_token)
        return data


class DeviceAwareTokenRefreshSerializer(TokenRefreshSerializer):
    """Ротация refresh с тремя проверками поверх стандартной (§7.4).

    1) Переиспользование уже ротированного токена — признак кражи: инвалидируем
       всю цепочку пользователя, а не только предъявленный токен.
    2) Версия токенов: смена пароля и logout-all убивают старые цепочки.
    3) Устройство не отозвано.
    """

    def validate(self, attrs):
        payload = tokens.decode_refresh_payload(attrs["refresh"])
        if payload is None:
            return super().validate(attrs)  # мусорный/протухший — стандартный invalid_token

        user = User.objects.filter(pk=payload.get("user_id")).first()

        # Порядок проверок важен: версия сверяется первой. Иначе токен, отозванный
        # явным действием владельца (смена пароля, logout-all), выглядел бы как
        # кража и запускал ещё один каскад отзыва.
        if user is None or payload.get(tokens.TOKEN_VERSION_CLAIM) != user.token_version:
            raise exceptions.AuthenticationFailed("Токен отозван.", code="token_revoked")

        # Версия актуальна, но jti в блеклисте — предъявлен уже ротированный токен
        # живой цепочки, то есть у кого-то есть его копия: гасим всю цепочку.
        if tokens.is_blacklisted(payload):
            services.logout_everywhere(user=user)
            raise exceptions.AuthenticationFailed(
                "Обнаружено повторное использование refresh-токена, все сессии завершены.",
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
    email_verified = serializers.SerializerMethodField()
    plan = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["public_id", "email", "email_verified", "date_joined", "profile", "plan"]

    def get_email_verified(self, obj) -> bool:
        return obj.email_verified_at is not None

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


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value: str) -> str:
        return value.lower()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        try:
            pk = force_str(urlsafe_base64_decode(attrs["uid"]))
            user = User.objects.get(pk=pk, is_active=True)
        except (ValueError, TypeError, User.DoesNotExist):
            raise serializers.ValidationError({"uid": ["Ссылка недействительна."]}) from None

        if not default_token_generator.check_token(user, attrs["token"]):
            raise serializers.ValidationError({"token": ["Ссылка недействительна или истекла."]})

        validate_password(attrs["new_password"], user=user)
        attrs["user"] = user
        return attrs


class EmailVerifyConfirmSerializer(serializers.Serializer):
    token = serializers.CharField()

    def validate_token(self, value: str) -> str:
        public_id = tokens.read_email_verify_token(value)
        if public_id is None:
            raise serializers.ValidationError("Ссылка недействительна или истекла.")
        user = User.objects.filter(public_id=public_id, is_active=True).first()
        if user is None:
            raise serializers.ValidationError("Ссылка недействительна или истекла.")
        self.context["target_user"] = user
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
