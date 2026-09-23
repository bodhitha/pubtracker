from django.urls import path
from . import views

urlpatterns = [
    path("", views.work_list, name="work_list"),
    path("export.<str:fmt>", views.work_export, name="work_export"),
    path("works/new/", views.work_new, name="work_new"),
    path("works/<str:code>/", views.work_detail, name="work_detail"),
    path("works/<str:code>/edit/", views.work_edit, name="work_edit"),
    path("works/<str:code>/add-me/", views.work_add_me, name="work_add_me"),
    path("people/new/", views.person_new, name="person_new"),
    path("people/", views.person_list, name="person_list"),
    path("people/<slug:slug>/", views.person_detail, name="person_detail"),
    path("healthz", views.healthz, name="healthz"),
]
