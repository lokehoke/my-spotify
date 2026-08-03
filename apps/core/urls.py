from django.urls import path

from apps.core import views

urlpatterns = [
    path("ping", views.PingView.as_view()),
]
