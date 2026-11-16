from django.urls import include, path
urlpatterns = [path('api/pwanimate/', include('pwanimate.api.urls')),
    path('pwanimate/', include('pwanimate.urls'))]
