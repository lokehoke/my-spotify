from django.urls import path

from apps.users import views

urlpatterns = [
    path("auth/register", views.RegisterView.as_view()),
    path("auth/token", views.TokenObtainView.as_view()),
    path("auth/token/refresh", views.DeviceAwareTokenRefreshView.as_view()),
    path("auth/logout", views.LogoutView.as_view()),
    path("auth/logout/all", views.LogoutAllView.as_view()),
    path("auth/email/verify/request", views.EmailVerifyRequestView.as_view()),
    path("auth/email/verify/confirm", views.EmailVerifyConfirmView.as_view()),
    path("auth/password/reset", views.PasswordResetRequestView.as_view()),
    path("auth/password/reset/confirm", views.PasswordResetConfirmView.as_view()),
    path("me", views.MeView.as_view()),
    path("me/export", views.MeExportView.as_view()),
    path("me/password", views.PasswordChangeView.as_view()),
    path("me/devices", views.DeviceListView.as_view()),
    path("me/devices/<int:pk>", views.DeviceRevokeView.as_view()),
    path("me/subscription", views.MySubscriptionView.as_view()),
    path("plans", views.PlanListView.as_view()),
]
