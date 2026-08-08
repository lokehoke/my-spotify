from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.core import views as core_views

urlpatterns = [
    path("admin/", admin.site.urls),
    # Healthchecks — вне /api/v1/ и мимо DRF-троттлинга (ARCHITECTURE.md §7.13)
    path("healthz", core_views.healthz),
    path("readyz", core_views.readyz),
    path("api/v1/", include("apps.core.urls")),
    path("api/v1/", include("apps.users.urls")),
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="schema"),
]

if settings.DEBUG:
    urlpatterns += [
        path("api/v1/docs/", SpectacularSwaggerView.as_view(url_name="schema")),
    ]
