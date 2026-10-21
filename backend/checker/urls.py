from django.urls import path
from . import views

urlpatterns = [
    path("auth/session/", views.session),
    path("auth/login/", views.sign_in),
    path("auth/logout/", views.sign_out),
    path("auth/password/", views.change_password),
    path("campaigns/", views.campaigns),
    path("lookups/", views.lookup),
    path("history/", views.history),
    path("history/<uuid:record_id>/", views.history_detail),
]
