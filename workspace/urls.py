from django.urls import path

from . import views

app_name = "workspace"

urlpatterns = [
    path("sync/", views.workspace_sync, name="sync"),
    path("dismiss/", views.workspace_dismiss, name="dismiss"),
]
