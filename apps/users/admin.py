from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from apps.users.models import Plan, Subscription, User, UserDevice, UserProfile


class ProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("username",)
    list_display = ("username", "is_staff", "is_active", "date_joined")
    search_fields = ("username",)
    inlines = [ProfileInline]
    filter_horizontal = ("groups", "user_permissions")
    readonly_fields = ("public_id", "last_login", "date_joined")
    fieldsets = (
        (None, {"fields": ("public_id", "username", "password")}),
        (
            "Права",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Даты", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("username", "password1", "password2")}),
    )

    def save_model(self, request, obj, form, change):
        """Форма админки сохраняет объект напрямую, минуя UserManager, — а профиль
        создаётся именно там. Без этого созданный в админке пользователь ронял
        /me в 500."""
        super().save_model(request, obj, form, change)
        UserProfile.objects.get_or_create(user=obj, defaults={"display_name": obj.username})


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "price_cents", "max_quality", "is_active")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "status", "current_period_end")
    list_select_related = ("user", "plan")
    autocomplete_fields = ("user",)


@admin.register(UserDevice)
class UserDeviceAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "name", "last_seen_at", "revoked_at")
    list_select_related = ("user",)
