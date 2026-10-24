from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard),
    path("upload/", views.upload),
    path("queue/continue/", views.continue_next),
    path("safety/resume/", views.resume_safety),
    path("<uuid:batch_id>/", views.details),
    path("<uuid:batch_id>/control/", views.control),
    path("<uuid:batch_id>/download/", views.download),
    path("<uuid:batch_id>/partial-export/", views.prepare_partial),
    path("<uuid:batch_id>/partial-exports/<uuid:snapshot_id>/download/", views.download_partial),
    path("<uuid:batch_id>/numbers/<int:sequence>/", views.number_detail),
]
