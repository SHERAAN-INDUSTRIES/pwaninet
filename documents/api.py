from django.shortcuts import get_object_or_404
"""Django REST Framework API viewsets for the Document Repository."""

from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny, IsAuthenticatedOrReadOnly
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Q, Count
from users.models import GlobalRole
from .permissions import IsDocumentUploaderOrAdmin

from .models import (
    # Academic Domain
    AcademicYear,
    Semester,
    Faculty,
    School,
    Department,
    Programme,
    AcademicUnit,
    # Document Domain
    Category,
    Tag,
    Document,
    DocumentVersion,
    DocumentFile,
    DocumentAcademicUnit,
    # Engagement Domain
    DocumentBookmark,
    DocumentRating,
)
from .serializers import (
    AcademicYearSerializer,
    SemesterSerializer,
    FacultySerializer,
    SchoolSerializer,
    DepartmentSerializer,
    ProgrammeSerializer,
    AcademicUnitSerializer,
    CategorySerializer,
    TagSerializer,
    DocumentListSerializer,
    DocumentDetailSerializer,
    DocumentCreateSerializer,
    DocumentBookmarkSerializer,
    DocumentRatingSerializer,
)
from .selectors.document_selectors import DocumentSelector


# Academic Domain ViewSets

class AcademicYearViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint for academic years."""
    queryset = AcademicYear.objects.all()
    serializer_class = AcademicYearSerializer
    permission_classes = [AllowAny]
    filterset_fields = ['is_current']


class SemesterViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint for semesters."""
    queryset = Semester.objects.all()
    serializer_class = SemesterSerializer
    permission_classes = [AllowAny]
    filterset_fields = ['academic_year']


class FacultyViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint for faculties."""
    queryset = Faculty.objects.all()
    serializer_class = FacultySerializer
    permission_classes = [AllowAny]


class SchoolViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint for schools."""
    queryset = School.objects.all()
    serializer_class = SchoolSerializer
    permission_classes = [AllowAny]
    filterset_fields = ['faculty']


class DepartmentViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint for departments."""
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer
    permission_classes = [AllowAny]
    filterset_fields = ['school']


class ProgrammeViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint for programmes."""
    queryset = Programme.objects.all()
    serializer_class = ProgrammeSerializer
    permission_classes = [AllowAny]
    filterset_fields = ['department']


class AcademicUnitViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint for academic units."""
    queryset = AcademicUnit.objects.all()
    serializer_class = AcademicUnitSerializer
    permission_classes = [AllowAny]
    filterset_fields = ['is_active']
    search_fields = ['code', 'name']



# Document Domain ViewSets

class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint for document categories."""
    queryset = Category.objects.filter(is_active=True)
    serializer_class = CategorySerializer
    permission_classes = [AllowAny]


class TagViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint for document tags."""
    queryset = Tag.objects.all()
    serializer_class = TagSerializer
    permission_classes = [AllowAny]
    search_fields = ['name']


class DocumentViewSet(viewsets.ModelViewSet):
    lookup_field = "share_id"
    """API endpoint for documents."""
    permission_classes = [IsAuthenticatedOrReadOnly, IsDocumentUploaderOrAdmin]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['category', 'visibility', 'status', 'language']
    search_fields = ['title', 'description']
    ordering_fields = ['created_at', 'updated_at', 'published_at', 'title']
    ordering = ['-created_at']
    
    def get_permissions(self):
        """Dynamic permissions based on action."""
        if self.action in ['view', 'download', 'stats']:
            return [AllowAny()]
        elif self.action in ['bookmark', 'rate']:
            return [IsAuthenticated()]
        return [IsAuthenticatedOrReadOnly(), IsDocumentUploaderOrAdmin()]
    
    def get_queryset(self):
        """Filter queryset based on user and status."""
        queryset = Document.objects.select_related(
            'category', 'uploaded_by'
        ).prefetch_related(
            'academic_units__academic_unit',
            'document_tags__tag',
        )
        
        from documents.access_policy import DocumentAccessPolicy
        return queryset.filter(pk__in=DocumentAccessPolicy.authorized_documents(
            self.request.user, require_ready=self.action not in {'retrieve','update','partial_update','destroy'}).values('pk'))

    def perform_create(self, serializer):
        """Set uploaded_by on document creation."""
        serializer.save(uploaded_by=self.request.user)

    def perform_destroy(self, instance):
        """
        Soft-delete document by archiving unless hard_delete=true is requested by staff.
        """
        hard_delete = self.request.query_params.get('hard_delete', 'false').lower() == 'true'
        if hard_delete and (self.request.user.is_staff or self.request.user.is_superuser):
            instance.delete()
        else:
            instance.status = 'archived'
            instance.save(update_fields=['status'])
    
    def get_serializer_class(self):
        """Return appropriate serializer based on action."""
        if self.action == 'list':
            return DocumentListSerializer
        elif self.action == 'create':
            return DocumentCreateSerializer
        return DocumentDetailSerializer
    
    @action(detail=False, methods=['get'])
    def trending(self, request):
        """Get trending documents."""
        documents = DocumentSelector.get_trending_documents(limit=20)
        serializer = DocumentListSerializer(documents, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def recent(self, request):
        """Get recently added documents."""
        limit = int(request.query_params.get('limit', 20))
        documents = DocumentSelector.list_documents_for_home(limit=limit)
        serializer = DocumentListSerializer(documents, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def by_category(self, request):
        """Get documents by category."""
        category_code = request.query_params.get('category')
        limit = int(request.query_params.get('limit', 20))
        documents = DocumentSelector.get_documents_by_category(category_code, limit)
        serializer = DocumentListSerializer(documents, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def by_unit(self, request):
        """Get documents by academic unit."""
        unit_code = request.query_params.get('unit')
        limit = int(request.query_params.get('limit', 20))
        documents = DocumentSelector.get_documents_by_academic_unit(unit_code, limit)
        serializer = DocumentListSerializer(documents, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def bookmark(self, request, pk=None):
        """Bookmark a document."""
        document = self.get_object()
        bookmark, created = DocumentBookmark.objects.get_or_create(
            document=document,
            user=request.user,
            defaults={'is_favorite': False}
        )
        
        if request.data.get('is_favorite') is not None:
            bookmark.is_favorite = request.data['is_favorite']
        
        if request.data.get('notes'):
            bookmark.notes = request.data['notes']
        
        bookmark.save()
        serializer = DocumentBookmarkSerializer(bookmark)
        return Response(serializer.data, status=status.HTTP_200_OK if not created else status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['post'])
    def rate(self, request, pk=None):
        """Rate a document."""
        document = self.get_object()
        rating, created = DocumentRating.objects.update_or_create(
            document=document,
            user=request.user,
            defaults={
                'rating': request.data['rating'],
                'review': request.data.get('review', '')
            }
        )
        serializer = DocumentRatingSerializer(rating)
        return Response(serializer.data, status=status.HTTP_200_OK if not created else status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['post'])
    def view(self, request, pk=None):
        """Record a document view."""
        document = self.get_object()
        from .engagement.models import DocumentView
        DocumentView.objects.create(
            document=document,
            user=request.user if request.user.is_authenticated else None,
            session_key=request.session.session_key,
            ip_address=self.get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )
        return Response({'status': 'recorded'}, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['get'])
    def stats(self, request, pk=None):
        """Get document statistics (view count, download count, bookmark count)."""
        document = self.get_object()
        
        from .engagement.models import DocumentView, DocumentDownload, DocumentBookmark
        
        return Response({
            'view_count': document.views.count(),
            'download_count': document.downloads.count(),
            'bookmark_count': document.bookmarks.count(),
            'rating_count': document.ratings.count(),
        })
    
    @action(detail=True, methods=['post'])
    def download(self, request, pk=None):
        """Record a document download."""
        document = self.get_object()
        from documents.access_policy import DocumentAccessPolicy
        if not DocumentAccessPolicy.can_read(request.user, document):
            from django.http import Http404
            raise Http404('Document not found')
        file_id = request.data.get('file_id')
        
        from .engagement.models import DocumentDownload
        from .documents.models import DocumentFile
        
        document_file = get_object_or_404(DocumentFile, id=file_id, document_version__document=document) if file_id else document.latest_version.files.first()
        
        DocumentDownload.objects.create(
            document=document,
            document_file=document_file,
            user=request.user if request.user.is_authenticated else None,
            session_key=request.session.session_key,
            ip_address=self.get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )
        
        return Response({
            'status': 'recorded',
            'file_url': document_file.storage_path if document_file else None
        }, status=status.HTTP_201_CREATED)
    
    def get_client_ip(self, request):
        """Get client IP address."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip


class UserDocumentViewSet(viewsets.ReadOnlyModelViewSet):
    lookup_field = "share_id"
    """API endpoint for user's documents."""
    permission_classes = [IsAuthenticated]
    serializer_class = DocumentListSerializer
    
    def get_queryset(self):
        """Get documents for the authenticated user."""
        document_type = self.request.query_params.get('type', 'uploads')
        limit = int(self.request.query_params.get('limit', 20))
        
        documents = DocumentSelector.list_documents_for_user(
            user_id=self.request.user.id,
            document_type=document_type,
            limit=limit
        )
        return documents
