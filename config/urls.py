from django.contrib import admin
from django.urls import include, path

admin.site.site_header = admin.site.site_title = "Publication tracker admin"
urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("pubs.urls")),
]
