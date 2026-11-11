from django.urls import path
from . import views

app_name = 'calling'
urlpatterns = [
    path('', views.collection, name='collection'),
    path('<uuid:call_id>/', views.detail, name='detail'),
    path('<uuid:call_id>/connection/', views.connection, name='connection'),
]
