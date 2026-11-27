from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [path("", include("core.urls"))]
if settings.DJANGO_ADMIN_ENABLED:
    urlpatterns.insert(0, path("admin/", admin.site.urls))

handler400 = "core.views.bad_request"
handler403 = "core.views.permission_denied"
handler404 = "core.views.not_found"
handler500 = "core.views.server_error"
