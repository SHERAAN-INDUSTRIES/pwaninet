from django.urls import include, path
from django.http import HttpResponse
from documents.views import serve_download

def placeholder(request, share_id):
    return HttpResponse('Synthetic route only')

urlpatterns = [
    path('api/pwanimate/', include('pwanimate.api.urls')),
    path('pwanimate/', include('pwanimate.urls')),
    path('documents/', include(([
        path('document/<uuid:share_id>/download/', serve_download, name='serve_download'),
        path('document/<uuid:share_id>/', placeholder, name='document_detail'),
    ], 'documents'), namespace='documents')),
]
