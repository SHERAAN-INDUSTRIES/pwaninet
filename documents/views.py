import logging
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import TemplateView
from django.contrib.auth.decorators import login_required
from django.db import models
from django.db.models import Q
from django.core.cache import cache
from django.utils import timezone
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)

from .models import (
    Category,
    Tag,
    Document,
    DocumentBookmark,
    DocumentDownload,
    DocumentView,
)
from documents.engagement.models import DocumentRating, DocumentShare, DocumentAnalytics
from .selectors.document_selectors import DocumentSelector
from pwaninet.utils.htmx import htmx_location_response


# ============== PAGE VIEWS ==============

def repository_home(request):
    """
    Repository landing page with search, categories, and document sections.
    """
    # Get categories
    categories = Category.objects.filter(is_active=True)
    
    # Get trending documents with personalization
    trending_documents = DocumentSelector.get_trending_documents(
        limit=10,
        user=request.user if request.user.is_authenticated else None
    )
    
    # Get recently added documents with personalization
    recent_documents = DocumentSelector.list_documents_for_home(
        limit=10,
        user=request.user if request.user.is_authenticated else None
    )
    
    # Get popular documents with personalization
    popular_documents = DocumentSelector.get_popular_documents(
        limit=10,
        user=request.user if request.user.is_authenticated else None
    )

    # Get personalized "For You" documents from recommendation engine
    from recommendations.services.engine import UnifiedRecommendationEngine
    for_you_documents = []
    if request.user.is_authenticated:
        for_you_documents = UnifiedRecommendationEngine.get_for_you_documents(
            user=request.user,
            limit=8
        )

    # Get recent searches from session (simpler approach)
    recent_searches = request.session.get('recent_searches', [])

    context = {
        'page_title': 'Document Repository',
        'categories': categories,
        'for_you_documents': for_you_documents,
        'trending_documents': trending_documents,
        'recent_documents': recent_documents,
        'popular_documents': popular_documents,
        'recent_searches': recent_searches,
        'document_content_partial': 'documents/partials/home_content.html',
        'show_library_button': True,
        'show_upload_button': True,
    }
    if request.headers.get('HX-Request'):
        return render(request, 'documents/partials/documents_navigation_partial.html', context)
    return render(request, 'documents/home.html', context)


def search_results(request):
    """
    Search results page with filters and sorting.
    """
    query = request.GET.get('q', '')
    category = request.GET.get('category')
    academic_unit = request.GET.get('unit')
    academic_level = request.GET.get('academic_level')
    semester = request.GET.get('semester')
    academic_year = request.GET.get('academic_year')
    programme = request.GET.get('programme')
    school = request.GET.get('school')
    department = request.GET.get('department')
    file_type = request.GET.get('file_type')
    sort_by = request.GET.get('sort', 'relevance')
    
    # Personalized filters
    my_programme = request.GET.get('my_programme')
    my_units = request.GET.get('my_units')
    my_semester = request.GET.get('my_semester')
    my_level = request.GET.get('my_level')
    my_academic_year = request.GET.get('my_academic_year')
    
    # Save search to session if query exists
    if query:
        recent_searches = request.session.get('recent_searches', [])
        # Add query if not already in recent searches
        if query not in [s if isinstance(s, str) else s.get('query', '') for s in recent_searches]:
            recent_searches.insert(0, query)
            # Keep only last 5 searches
            recent_searches = recent_searches[:5]
            request.session['recent_searches'] = recent_searches
    
    filters = {}
    if category:
        filters['category'] = category
    if academic_unit:
        filters['academic_unit'] = academic_unit
    if academic_level:
        filters['academic_level'] = academic_level
    if semester:
        filters['semester'] = semester
    if academic_year:
        filters['academic_year'] = academic_year
    if programme:
        filters['programme'] = programme
    if school:
        filters['school'] = school
    if department:
        filters['department'] = department
    if file_type:
        filters['file_type'] = file_type
    
    # Apply personalized filters if user is authenticated
    if request.user.is_authenticated:
        if my_programme and request.user.programme:
            filters['programme'] = request.user.programme.id
        if my_units:
            from .academic.services import StudentAcademicEnrollmentService
            active_enrollments = StudentAcademicEnrollmentService.get_active_enrollments(request.user)
            if active_enrollments:
                filters['academic_units'] = [enr.academic_unit_id for enr in active_enrollments]
            elif request.user.programme:
                from .academic.models import ProgrammeUnit
                user_units = ProgrammeUnit.objects.filter(
                    programme=request.user.programme
                ).values_list('academic_unit_id', flat=True)
                if user_units:
                    filters['academic_units'] = list(user_units)
        if my_semester and request.user.semester:
            filters['semester'] = request.user.semester.id
        if my_level and request.user.academic_level:
            filters['academic_level'] = request.user.academic_level.id
        if my_academic_year and request.user.academic_year:
            filters['academic_year'] = request.user.academic_year.id
    
    # Get "Did you mean" suggestion
    from search.services.unified_search_service import UnifiedSearchService
    unified_service = UnifiedSearchService()
    did_you_mean = unified_service.get_document_did_you_mean(query) if query else None
    
    documents = DocumentSelector.search_documents(
        query=query,
        filters=filters,
        limit=50,
        user=request.user if request.user.is_authenticated else None,
        sort_by=sort_by,
    )
    
    # Get categories for filter dropdown
    from .models import Category
    categories = Category.objects.filter(is_active=True)
    
    # Get academic entities for filter dropdown
    from .academic.models import AcademicUnit, Semester, AcademicYear, AcademicLevel, Programme, School, Department
    academic_units = AcademicUnit.objects.filter(is_active=True)[:50]
    semesters = Semester.objects.all()
    academic_years = AcademicYear.objects.all()
    academic_levels = AcademicLevel.objects.filter(is_active=True)
    programmes = Programme.objects.filter(is_active=True)
    schools = School.objects.all()
    departments = Department.objects.all()
    
    context = {
        'page_title': 'Search Results',
        'query': query,
        'documents': documents,
        'category': category,
        'academic_unit': academic_unit,
        'academic_level': academic_level,
        'semester': semester,
        'academic_year': academic_year,
        'programme': programme,
        'school': school,
        'department': department,
        'file_type': file_type,
        'sort_by': sort_by,
        'my_programme': my_programme,
        'my_units': my_units,
        'my_semester': my_semester,
        'my_level': my_level,
        'my_academic_year': my_academic_year,
        'categories': categories,
        'academic_units': academic_units,
        'semesters': semesters,
        'academic_years': academic_years,
        'academic_levels': academic_levels,
        'programmes': programmes,
        'schools': schools,
        'departments': departments,
        'did_you_mean': did_you_mean,
        'document_content_partial': 'documents/partials/search_content.html',
        'show_library_button': True,
        'show_upload_button': True,
    }
    if request.headers.get('HX-Request'):
        return render(request, 'documents/partials/documents_navigation_partial.html', context)
    return render(request, 'documents/search.html', context)


def clear_recent_searches(request):
    """
    Clear recent searches from session.
    """
    request.session['recent_searches'] = []
    if request.headers.get('HX-Request'):
        return htmx_location_response(request.META.get('HTTP_REFERER', '/documents/search/'))
    from django.http import HttpResponseRedirect
    return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/documents/search/'))


def search_suggestions_view(request):
    """
    Autocomplete search suggestions endpoint returning JSON.
    """
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'suggestions': []})

    from search.services.unified_search_service import UnifiedSearchService
    search_service = UnifiedSearchService()
    suggestions = search_service.get_document_suggestions(q)
    return JsonResponse({'suggestions': suggestions})


def document_detail(request, share_id):
    """
    Individual document detail page with preview and metadata.
    """
    document = DocumentSelector.get_document_with_relations(share_id)
    
    if not document:
        from django.http import Http404
        raise Http404("Document not found")
    if (document.is_ai_generated or document.visibility == 'private') and (
        not request.user.is_authenticated or document.uploaded_by_id != request.user.id
    ):
        raise Http404("Document not found")
    
    # Record view asynchronously with caching
    cache_key = f'doc_view_{share_id}_{request.session.session_key or request.META.get("REMOTE_ADDR")}'
    if not cache.get(cache_key):
        # Create view record
        if request.user.is_authenticated:
            DocumentView.objects.create(
                document=document,
                user=request.user,
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', ''),
            )
        else:
            DocumentView.objects.create(
                document=document,
                session_key=request.session.session_key or 'anon',
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', ''),
            )
        # Cache for 5 minutes to prevent duplicate views
        cache.set(cache_key, True, 300)
    
    # Get cached view count or fetch from database
    view_count_cache_key = f'doc_view_count_{share_id}'
    cached_view_count = cache.get(view_count_cache_key)
    if cached_view_count is None:
        # Cache view count for 10 minutes
        cached_view_count = document.views.count()
        cache.set(view_count_cache_key, cached_view_count, 600)
    
    # Get cached download count
    download_count_cache_key = f'doc_download_count_{share_id}'
    cached_download_count = cache.get(download_count_cache_key)
    if cached_download_count is None:
        # Cache download count for 10 minutes
        cached_download_count = document.downloads.count()
        cache.set(download_count_cache_key, cached_download_count, 600)
    
    # Get cached bookmark count
    bookmark_count_cache_key = f'doc_bookmark_count_{share_id}'
    cached_bookmark_count = cache.get(bookmark_count_cache_key)
    if cached_bookmark_count is None:
        # Cache bookmark count for 10 minutes
        cached_bookmark_count = document.bookmarks.count()
        cache.set(bookmark_count_cache_key, cached_bookmark_count, 600)
    
    # Get related documents using improved relevance algorithm
    related_documents = DocumentSelector.get_related_documents(document, limit=6)
    
    # Get primary academic unit
    primary_unit = None
    if hasattr(document, 'academic_units'):
        primary_unit = document.academic_units.filter(is_primary=True).first()
    
    # Check if user has bookmarked this document
    is_bookmarked = False
    if request.user.is_authenticated:
        is_bookmarked = document.bookmarks.filter(user=request.user).exists()
    
    # Get user's rating
    user_rating = None
    if request.user.is_authenticated:
        rating_obj = document.ratings.filter(user=request.user).first()
        if rating_obj:
            user_rating = rating_obj.rating
    
    # Get or create analytics
    analytics, _ = DocumentAnalytics.objects.get_or_create(document=document)
    can_manage = user_can_manage_document(request.user, document)
    versions = document.versions.prefetch_related('files').order_by('-version_number')
    
    
    # Generate download token
    from django.core.signing import TimestampSigner
    signer = TimestampSigner()
    download_token = signer.sign_object(str(document.share_id))
    
    context = {
        'page_title': document.title,
        'download_token': download_token,

        'document': document,
        'related_documents': related_documents,
        'primary_unit': primary_unit,
        'is_bookmarked': is_bookmarked,
        'user_rating': user_rating,
        'analytics': analytics,
        'can_manage': can_manage,
        'versions': versions,
        'document_content_partial': 'documents/partials/document_detail_content.html',
        'show_library_button': True,
        'show_upload_button': False,
    }
    if request.headers.get('HX-Request'):
        return render(request, 'documents/partials/documents_navigation_partial.html', context)
    return render(request, 'documents/document_detail.html', context)


@login_required
@require_GET
def private_resource_asset(request, file_id, asset):
    """Serve a generated file/preview only to the student who owns its document."""
    from documents.documents.models import DocumentFile
    document_file = get_object_or_404(
        DocumentFile.objects.select_related('document_version__document'),
        pk=file_id,
        storage_private=True,
    )
    document = document_file.document_version.document
    if document.uploaded_by_id != request.user.id or not document.is_ai_generated:
        raise Http404("Resource not found")

    if asset == 'original':
        if not document_file.file:
            raise Http404("Resource file not found")
        file_handle = document_file.file.open('rb')
        content_type = document_file.mime_type or 'application/octet-stream'
        filename = document_file.original_filename
    elif asset in {'preview', 'thumbnail'}:
        storage_path = document_file.preview_path if asset == 'preview' else document_file.thumbnail_path
        if not storage_path:
            raise Http404("Resource preview not found")
        file_handle = document_file.file.storage.open(storage_path, 'rb')
        content_type = 'image/jpeg'
        filename = f"resource-{file_id}.jpg"
    else:
        raise Http404("Resource asset not found")

    response = FileResponse(file_handle, content_type=content_type)
    safe_filename = filename.replace('"', '').replace('\r', '').replace('\n', '')
    response['Content-Disposition'] = f'inline; filename="{safe_filename}"'
    response['Cache-Control'] = 'private, no-store, max-age=0'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def user_can_manage_document(user, document) -> bool:
    """Check if the user has permission to edit, delete, or manage the document."""
    if not user or not user.is_authenticated:
        return False
    if document.is_ai_generated:
        return document.uploaded_by_id == user.id
    if document.uploaded_by_id == user.id:
        return True
    if user.is_staff or user.is_superuser:
        return True
    from users.models import GlobalRole
    if getattr(user, 'global_role', None) in [GlobalRole.PRESIDENT, GlobalRole.DELEGATE]:
        return True
    return False


@login_required
def edit_document(request, share_id):
    """Edit document metadata (title, description, category, academic unit, visibility, language)."""
    document = Document.objects.filter(share_id=share_id).first()
    if not document:
        return JsonResponse({'success': False, 'error': 'Document not found'}, status=404)

    if not user_can_manage_document(request.user, document):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)

    from .academic.models import AcademicUnit, Programme
    from .models import DocumentAcademicUnit

    if request.method == 'GET':
        categories = Category.objects.filter(is_active=True).order_by('name')
        programmes = Programme.objects.filter(is_active=True).order_by('name')
        academic_units = AcademicUnit.objects.filter(is_active=True).order_by('name')
        primary_academic_unit = document.academic_units.filter(is_primary=True).first()

        context = {
            'document': document,
            'categories': categories,
            'programmes': programmes,
            'academic_units': academic_units,
            'primary_academic_unit': primary_academic_unit,
        }
        return render(request, 'documents/partials/edit_document_modal.html', context)

    # POST update
    title = request.POST.get('title', '').strip()
    if not title:
        return JsonResponse({'success': False, 'error': 'Title is required'}, status=400)

    description = request.POST.get('description', '').strip()
    category_id = request.POST.get('category')
    visibility = request.POST.get('visibility', document.visibility)
    language = request.POST.get('language', document.language)
    academic_unit_id = request.POST.get('academic_unit')

    if category_id:
        try:
            document.category = Category.objects.get(id=category_id)
        except Category.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Invalid category'}, status=400)

    document.title = title
    document.description = description
    if document.is_ai_generated:
        # Generated resources stay private even if a crafted form submits a
        # different visibility value. Sharing needs an explicit recipient ACL.
        document.visibility = 'private'
    elif visibility in ['public', 'private', 'restricted']:
        document.visibility = visibility
    if language in ['en', 'sw', 'fr', 'other']:
        document.language = language
    document.save()

    # Update academic unit resiliently
    if academic_unit_id:
        from .academic.services import DocumentAcademicLinkService
        DocumentAcademicLinkService.link_document_to_unit(
            document=document,
            academic_unit_id=academic_unit_id,
            academic_level_id=request.POST.get('academic_level'),
            semester_id=request.POST.get('semester'),
            academic_year_id=request.POST.get('academic_year'),
            uploader=request.user,
            replace_existing=True,
        )

    # Update search index
    try:
        from documents.services.search_service import SearchService
        SearchService().index_document(document)
    except Exception as e:
        logger.warning(f"Error re-indexing document {document.id} after edit: {e}")

    if request.headers.get('HX-Request'):
        response = HttpResponse(status=200)
        response['HX-Refresh'] = 'true'
        return response

    from django.contrib import messages
    messages.success(request, 'Document updated successfully.')
    return redirect('documents:document_detail', share_id=document.id)


@login_required
def delete_document_modal(request, share_id):
    """Return confirmation modal HTML for document deletion."""
    document = Document.objects.filter(share_id=share_id).first()
    if not document:
        return JsonResponse({'success': False, 'error': 'Document not found'}, status=404)

    if not user_can_manage_document(request.user, document):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)

    return render(request, 'documents/partials/delete_document_modal.html', {'document': document})


@login_required
def delete_document(request, share_id):
    """Soft-delete a document (archive)."""
    document = Document.objects.filter(share_id=share_id).first()
    if not document:
        return JsonResponse({'success': False, 'error': 'Document not found'}, status=404)

    if not user_can_manage_document(request.user, document):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)

    if request.method == 'POST':
        document.archive()

        # Remove from search index
        try:
            from documents.search.models import DocumentSearchIndex
            DocumentSearchIndex.objects.filter(document=document).delete()
        except Exception as e:
            logger.warning(f"Error removing document {document.id} from search index: {e}")

        from django.contrib import messages
        messages.success(request, 'Document has been deleted.')

        if request.headers.get('HX-Request'):
            referer = request.META.get('HTTP_REFERER', '')
            if f'/document/{share_id}' in referer:
                return htmx_location_response(request.build_absolute_uri(reverse('documents:my_library')))
            return HttpResponse(status=200)

        return redirect('documents:my_library')

    return JsonResponse({'error': 'POST required'}, status=405)


@login_required
def toggle_document_availability(request, share_id):
    """Toggle document availability (mark unavailable / available)."""
    document = Document.objects.filter(share_id=share_id).first()
    if not document:
        return JsonResponse({'success': False, 'error': 'Document not found'}, status=404)

    if not user_can_manage_document(request.user, document):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)

    if request.method == 'POST':
        new_avail = document.toggle_availability()

        try:
            from documents.services.search_service import SearchService
            SearchService().index_document(document)
        except Exception as e:
            logger.warning(f"Error updating search index after availability toggle for {document.id}: {e}")

        msg = "Document is now available to students." if new_avail else "Document marked unavailable."
        from django.contrib import messages
        messages.success(request, msg)

        if request.headers.get('HX-Request'):
            response = HttpResponse(status=200)
            response['HX-Refresh'] = 'true'
            return response

        return redirect('documents:document_detail', share_id=document.id)

    return JsonResponse({'error': 'POST required'}, status=405)


@login_required
def upload_new_version(request, share_id):
    """Upload a new version of an existing document.

    LEGACY PATH: the bytes are posted through Django. The version modal now
    uses the direct-to-R2 engine (uploads/ app, kind='document' with
    target='version'); this endpoint stays for API clients.
    """
    document = Document.objects.filter(share_id=share_id).first()
    if not document:
        return JsonResponse({'success': False, 'error': 'Document not found'}, status=404)

    if not user_can_manage_document(request.user, document):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)

    if request.method == 'GET':
        return render(request, 'documents/partials/new_version_modal.html', {'document': document})

    # POST
    new_file = request.FILES.get('file')
    if not new_file:
        return JsonResponse({'success': False, 'error': 'No file uploaded'}, status=400)

    change_notes = request.POST.get('change_notes', '').strip()

    from documents.services.upload_service import UploadService
    service = UploadService()

    try:
        version = service.create_document_version(
            document=document,
            file=new_file,
            user=request.user,
            change_notes=change_notes,
        )
        return JsonResponse({
            'success': True,
            'message': f'Version {version.version_number} uploaded successfully and is being processed.',
            'version_number': version.version_number,
        })
    except ValueError as ve:
        return JsonResponse({'success': False, 'error': str(ve)}, status=400)
    except Exception as e:
        logger.error(f"Error uploading new version for document {document.id}: {e}", exc_info=True)
        return JsonResponse({'success': False, 'error': f'Upload failed: {str(e)}'}, status=500)



from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods


@require_http_methods(["GET", "POST"])
@login_required
def upload_document(request):
    """
    Multi-stage upload flow for documents with student academic defaults.

    LEGACY PATH: the file bytes are posted through Django and written to
    storage by the app server. The upload UI now uses the direct-to-R2
    engine instead (uploads/ app, kind='document'): the browser PUTs the
    bytes straight into the bucket and this flow is only kept for API
    clients that cannot use the engine.
    """
    from .academic.models import AcademicUnit, Semester, AcademicYear, AcademicLevel, Programme
    from .academic.services import StudentAcademicEnrollmentService, DocumentAcademicLinkService

    # Ensure an 'Other' category is available for memos / announcements / general documents
    other_cat = Category.objects.filter(Q(code__iexact='other') | Q(name__iexact='Other')).first()
    if not other_cat:
        try:
            other_cat = Category.objects.create(
                name='Other',
                code='other',
                description='General announcements, memos, and non-unit documents',
                is_active=True,
            )
        except Exception:
            other_cat = Category.objects.filter(is_active=True).last()

    if request.method == 'POST':
        # Handle file upload
        files = request.FILES.getlist('files')
        raw_academic_unit = (request.POST.get('academic_unit') or '').strip()
        raw_category = (request.POST.get('category') or '').strip()
        academic_year_id = (request.POST.get('academic_year') or '').strip() or None
        raw_semester = (request.POST.get('semester') or '').strip()
        raw_level = (request.POST.get('academic_level') or '').strip()
        tags = request.POST.get('tags', '')

        is_other_unit = raw_academic_unit.lower() == 'other'
        academic_unit_id = None if (not raw_academic_unit or is_other_unit) else raw_academic_unit

        if (not raw_category or raw_category.lower() == 'other') and other_cat:
            category_id = other_cat.id
        elif is_other_unit and other_cat and not raw_category:
            category_id = other_cat.id
        else:
            category_id = raw_category

        if not files:
            from django.http import JsonResponse
            return JsonResponse({'success': False, 'error': 'No files uploaded'}, status=400)

        if not category_id:
            from django.http import JsonResponse
            return JsonResponse({'success': False, 'error': 'Category is required'}, status=400)

        # Resolve level & semester (accepts either level number 1..4 / semester number 1..2 or model PKs)
        resolved_level = StudentAcademicEnrollmentService.resolve_or_ensure_academic_level(raw_level) if raw_level else None
        resolved_sem = StudentAcademicEnrollmentService.resolve_or_ensure_semester(raw_semester) if raw_semester else None
        academic_level_id = resolved_level.id if resolved_level else None
        semester_id = resolved_sem.id if resolved_sem else None
        if not academic_year_id and resolved_sem and resolved_sem.academic_year_id:
            academic_year_id = resolved_sem.academic_year_id

        from .services.upload_service import UploadService
        upload_service = UploadService()
        for file in files:
            is_valid, error_msg = upload_service.validate_file(file)
            if not is_valid:
                from django.http import JsonResponse
                return JsonResponse({'success': False, 'error': f"{file.name}: {error_msg}"}, status=400)

        try:
            # Create documents with status 'processing'
            from .models import Document, DocumentFile, DocumentVersion, DocumentAcademicUnit, DocumentTag, Tag

            created_documents = []
            skipped_documents = []

            for index, file in enumerate(files):
                # Get individual file metadata if provided
                raw_title = request.POST.get(f'file_{index}_title')
                if raw_title and raw_title.strip():
                    title = raw_title.strip()
                else:
                    title = file.name.rsplit('.', 1)[0] if '.' in file.name else file.name
                description = request.POST.get(f'file_{index}_description', '')

                # Check if document with same title already exists for this user; ensure unique title
                base_title = title
                counter = 1
                while Document.objects.filter(title=title, uploaded_by=request.user).exists():
                    title = f"{base_title} ({counter})"
                    counter += 1

                # Create document (without academic unit/year/semester - those go in junction table)
                document = Document.objects.create(
                    title=title,
                    description=description,
                    uploaded_by=request.user,
                    status='processing',  # Start as processing, Celery will update to ready
                    visibility='public',
                    category_id=category_id
                )

                # Create document version
                document_version = DocumentVersion.objects.create(
                    document=document,
                    version_number=1,
                    is_latest=True,
                    created_by=request.user
                )

                # Create document file
                document_file = DocumentFile.objects.create(
                    document_version=document_version,
                    file=file,
                    original_filename=file.name,
                    size_bytes=file.size,
                    mime_type=file.content_type,
                    extension=file.name.split('.')[-1].lower() if '.' in file.name else '',
                    storage_path=file.name,
                    uploaded_by=request.user,
                    processing_status='pending',
                    checksum=None  # Will be generated by background processing
                )

                # Create academic unit relationship resiliently when a unit is selected (not 'other')
                if academic_unit_id:
                    DocumentAcademicLinkService.link_document_to_unit(
                        document=document,
                        academic_unit_id=academic_unit_id,
                        academic_level_id=academic_level_id,
                        semester_id=semester_id,
                        academic_year_id=academic_year_id,
                        uploader=request.user,
                        replace_existing=False,
                    )

                # Create tag relationships if tags provided
                if tags:
                    tag_names = [tag.strip() for tag in tags.split(',') if tag.strip()]
                    for tag_name in tag_names:
                        tag, created = Tag.objects.get_or_create(
                            name=tag_name,
                            defaults={'slug': tag_name.lower().replace(' ', '-')}
                        )
                        DocumentTag.objects.create(document=document, tag=tag)

                created_documents.append(document)

            # Trigger Celery background processing for each document
            from .tasks.processing import process_document
            task_ids = []
            for document in created_documents:
                task = process_document.delay(document.id)
                task_ids.append(task.id)

            from django.http import JsonResponse
            return JsonResponse({
                'success': True,
                'document_count': len(created_documents),
                'skipped_count': len(skipped_documents),
                'skipped_documents': skipped_documents,
                'task_ids': task_ids
            })

        except Exception as e:
            import traceback
            from django.http import JsonResponse
            return JsonResponse({'success': False, 'error': str(e), 'traceback': traceback.format_exc()}, status=500)

    # GET request - show upload form with student's Programme, Year, Semester, and Units pre-selected
    categories = Category.objects.filter(is_active=True).order_by('name')
    summary = StudentAcademicEnrollmentService.get_student_academic_summary(request.user)

    req_prog = request.GET.get('programme')
    if req_prog and str(req_prog).isdigit():
        selected_programme = Programme.objects.filter(pk=int(req_prog), is_active=True).first() or summary['programme']
    else:
        selected_programme = summary['programme']

    req_level = request.GET.get('level') or request.GET.get('year')
    if req_level and str(req_level).isdigit():
        selected_level_obj = StudentAcademicEnrollmentService.resolve_or_ensure_academic_level(int(req_level))
    else:
        selected_level_obj = summary['academic_level'] or StudentAcademicEnrollmentService.resolve_or_ensure_academic_level(1)

    req_sem = request.GET.get('semester')
    if req_sem and str(req_sem).isdigit() and int(req_sem) in (1, 2):
        selected_semester_num = int(req_sem)
    else:
        selected_semester_num = summary['semester_number'] if summary['semester_number'] in (1, 2) else 1

    req_unit_id = request.GET.get('unit')
    selected_unit_id = int(req_unit_id) if (req_unit_id and str(req_unit_id).isdigit()) else None

    if selected_unit_id and selected_programme and not (req_level or req_sem):
        from .academic.models import ProgrammeUnit
        pu_match = (
            ProgrammeUnit.objects.filter(programme=selected_programme, academic_unit_id=selected_unit_id)
            .select_related('academic_level', 'semester')
            .first()
        )
        if pu_match:
            if pu_match.academic_level:
                selected_level_obj = pu_match.academic_level
            if pu_match.semester and pu_match.semester.number in (1, 2):
                selected_semester_num = pu_match.semester.number

    # Ensure Year 1..4 AcademicLevel rows exist
    academic_levels = []
    for yr_num in range(1, 5):
        lvl = StudentAcademicEnrollmentService.resolve_or_ensure_academic_level(yr_num)
        if lvl:
            academic_levels.append(lvl)

    context_units = []
    if selected_programme:
        context_units = StudentAcademicEnrollmentService.get_units_for_context(
            programme_id=selected_programme.id,
            academic_level_ident=selected_level_obj.level if selected_level_obj else 1,
            semester_ident=selected_semester_num,
        )

    if selected_unit_id and not any(u['id'] == selected_unit_id for u in context_units):
        explicit_unit = AcademicUnit.objects.filter(pk=selected_unit_id, is_active=True).first()
        if explicit_unit:
            context_units.insert(0, {
                'id': explicit_unit.id,
                'code': explicit_unit.code,
                'name': explicit_unit.name,
                'is_elective': False,
                'shared_programme_codes': [],
                'document_count': 0,
            })

    programmes = Programme.objects.filter(is_active=True).select_related('department__school').order_by('name')
    semesters = Semester.objects.all()
    academic_years = AcademicYear.objects.all()

    context = {
        'page_title': 'Upload Document',
        'categories': categories,
        'other_category_id': other_cat.id if other_cat else '',
        'programmes': programmes,
        'academic_summary': summary,
        'selected_programme': selected_programme,
        'selected_level_obj': selected_level_obj,
        'selected_level_num': selected_level_obj.level if selected_level_obj else 1,
        'selected_semester_num': selected_semester_num,
        'selected_unit_id': selected_unit_id,
        'context_units': context_units,
        'academic_units': context_units,
        'semesters': semesters,
        'academic_years': academic_years,
        'academic_levels': academic_levels,
        'document_content_partial': 'documents/partials/upload_content.html',
        'show_library_button': True,
        'show_upload_button': False,
    }
    if request.headers.get('HX-Request'):
        return render(request, 'documents/partials/documents_navigation_partial.html', context)
    return render(request, 'documents/upload.html', context)


def upload_progress(request):
    """
    Upload progress tracking (for HTMX polling).
    """
    task_id = request.GET.get('task_id')
    
    if task_id:
        from celery.result import AsyncResult
        try:
            task = AsyncResult(task_id)
            from django.http import JsonResponse
            return JsonResponse({
                'status': task.status,
                'result': task.result if task.ready() else None,
            })
        except Exception as e:
            from django.http import JsonResponse
            return JsonResponse({'error': str(e)}, status=500)
    
    share_id = request.GET.get('share_id')
    
    if share_id:
        from .models import Document, DocumentFile
        try:
            document = Document.objects.get(share_id=share_id)
            document_file = DocumentFile.objects.filter(
                document_version__document=document
            ).first()
            
            from django.http import JsonResponse
            return JsonResponse({
                'status': document.status,
                'processing_status': document_file.processing_status if document_file else 'unknown',
                'has_preview': bool(document_file.preview_path) if document_file else False,
                'has_thumbnail': bool(document_file.thumbnail_path) if document_file else False,
            })
        except Document.DoesNotExist:
            from django.http import JsonResponse
            return JsonResponse({'error': 'Document not found'}, status=404)
    
    return render(request, 'documents/partials/upload_progress.html')


@login_required
def my_library(request):
    """
    Personal library dashboard with uploads, bookmarks, downloads.
    """
    user = request.user
    
    # Get statistics
    upload_count = Document.objects.filter(uploaded_by=user, is_ai_generated=False).exclude(status='archived').count()
    resource_count = Document.objects.filter(uploaded_by=user, is_ai_generated=True).exclude(status='archived').count()
    bookmark_count = DocumentBookmark.objects.filter(user=user).count()
    download_count = DocumentDownload.objects.filter(user=user).count()
    view_count = DocumentView.objects.filter(user=user).count()
    
    # Get recent activity
    recent_uploads = list(Document.objects.filter(
        uploaded_by=user, is_ai_generated=False
    ).exclude(
        status='archived'
    ).select_related(
        'category'
    ).prefetch_related(
        'versions__files',
        'academic_units__academic_unit'
    ).order_by('-created_at')[:5])
    recent_bookmarks = DocumentBookmark.objects.filter(user=user).select_related('document')[:5]
    has_processing_uploads = any(doc.is_processing for doc in recent_uploads)
    
    context = {
        'page_title': 'My Library',
        'upload_count': upload_count,
        'resource_count': resource_count,
        'bookmark_count': bookmark_count,
        'download_count': download_count,
        'view_count': view_count,
        'recent_uploads': recent_uploads,
        'recent_bookmarks': recent_bookmarks,
        'has_processing_uploads': has_processing_uploads,
        'document_content_partial': 'documents/partials/my_library_content.html',
        'show_library_button': False,
        'show_upload_button': True,
    }
    if request.headers.get('HX-Request'):
        return render(request, 'documents/partials/documents_navigation_partial.html', context)
    return render(request, 'documents/my_library.html', context)


@login_required
def clear_history(request):
    """
    Clear user's document view history.
    """
    if request.method == 'POST':
        DocumentView.objects.filter(user=request.user).delete()
        from django.contrib import messages
        messages.success(request, 'Your viewing history has been cleared.')
    if request.headers.get('HX-Request'):
        return htmx_location_response(request.META.get('HTTP_REFERER', '/documents/library/'))
    from django.http import HttpResponseRedirect
    return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/documents/library/'))


@login_required
def my_uploads(request):
    """
    User's uploaded documents.
    """
    documents = list(DocumentSelector.list_documents_for_user(
        user_id=request.user.id,
        document_type='uploads',
        limit=50
    ))
    has_processing_uploads = any(doc.is_processing for doc in documents)
    
    context = {
        'page_title': 'My Uploads',
        'documents': documents,
        'has_processing_uploads': has_processing_uploads,
        'document_content_partial': 'documents/partials/library_uploads_content.html',
        'show_library_button': False,
        'show_upload_button': True,
    }
    if request.headers.get('HX-Request'):
        return render(request, 'documents/partials/documents_navigation_partial.html', context)
    return render(request, 'documents/library_uploads.html', context)


@login_required
def my_resources(request):
    """List Pwanimate-generated resources owned by the current student."""
    documents = list(
        Document.objects.filter(
            uploaded_by=request.user,
            is_ai_generated=True,
        ).exclude(status='archived').select_related('category').prefetch_related(
            'versions__files',
        ).order_by('-created_at')[:100]
    )
    context = {
        'page_title': 'My Resources',
        'documents': documents,
        'resource_count': len(documents),
        'has_processing_uploads': any(doc.is_processing for doc in documents),
        'document_content_partial': 'documents/partials/library_resources_content.html',
        'show_library_button': False,
        'show_upload_button': False,
    }
    if request.headers.get('HX-Request'):
        return render(request, 'documents/partials/documents_navigation_partial.html', context)
    return render(request, 'documents/library_resources.html', context)


@login_required
def my_bookmarks(request):
    """
    User's bookmarked documents.
    """
    bookmarks = DocumentBookmark.objects.filter(
        user=request.user
    ).select_related('document').order_by('-created_at')
    
    context = {
        'page_title': 'My Bookmarks',
        'bookmarks': bookmarks,
        'document_content_partial': 'documents/partials/library_bookmarks_content.html',
        'show_library_button': False,
        'show_upload_button': True,
    }
    if request.headers.get('HX-Request'):
        return render(request, 'documents/partials/documents_navigation_partial.html', context)
    return render(request, 'documents/library_bookmarks.html', context)


@login_required
def my_downloads(request):
    """
    User's download history.
    """
    downloads = DocumentDownload.objects.filter(
        user=request.user
    ).select_related('document', 'document_file').order_by('-downloaded_at')
    
    context = {
        'page_title': 'My Downloads',
        'downloads': downloads,
        'document_content_partial': 'documents/partials/library_downloads_content.html',
        'show_library_button': False,
        'show_upload_button': True,
    }
    if request.headers.get('HX-Request'):
        return render(request, 'documents/partials/documents_navigation_partial.html', context)
    return render(request, 'documents/library_downloads.html', context)


@login_required
def my_history(request):
    """
    User's viewing history.
    """
    views = DocumentView.objects.filter(
        user=request.user
    ).select_related('document').order_by('-viewed_at')
    
    context = {
        'page_title': 'My History',
        'views': views,
        'document_content_partial': 'documents/partials/library_history_content.html',
        'show_library_button': False,
        'show_upload_button': True,
    }
    if request.headers.get('HX-Request'):
        return render(request, 'documents/partials/documents_navigation_partial.html', context)
    return render(request, 'documents/library_history.html', context)


# ============== ENGAGEMENT HTMX ENDPOINTS ==============

@csrf_exempt
@require_POST
@login_required
def toggle_bookmark(request, share_id):
    """
    Toggle bookmark status for a document via HTMX.
    """
    try:
        document = Document.objects.get(share_id=share_id)

        bookmark, created = DocumentBookmark.objects.get_or_create(
            document=document,
            user=request.user
        )
        
        if not created:
            # Remove bookmark if it already exists
            bookmark.delete()
            is_bookmarked = False
        else:
            is_bookmarked = True
        
        # Trigger analytics update asynchronously
        from .tasks.processing import update_document_analytics
        update_document_analytics.delay(document.id)
        
        # Return partial HTML response
        context = {
            'document': document,
            'is_bookmarked': is_bookmarked,
            'bookmark_count': document.bookmarks.count(),
        }
        return render(request, 'documents/partials/bookmark_button.html', context)
        
    except Document.DoesNotExist:
        return JsonResponse({'error': 'Document not found'}, status=404)


@csrf_exempt
@require_POST
@login_required
def rate_document(request, share_id):
    """
    Rate a document with thumbs up/down via HTMX.
    """
    try:
        document = Document.objects.get(share_id=share_id)
        rating_value = int(request.POST.get('rating', 0))
        
        if rating_value not in [1, -1]:
            return JsonResponse({'error': 'Invalid rating value'}, status=400)
        
        # Get or create rating
        rating, created = DocumentRating.objects.get_or_create(
            document=document,
            user=request.user,
            defaults={'rating': rating_value}
        )
        
        if not created:
            # Update existing rating
            rating.rating = rating_value
            rating.save()
        
        # Trigger analytics update asynchronously
        from .tasks.processing import update_document_analytics
        update_document_analytics.delay(document.id)
        
        # Get updated analytics
        analytics, _ = DocumentAnalytics.objects.get_or_create(document=document)
        
        # Return partial HTML response
        context = {
            'document': document,
            'user_rating': rating_value,
            'analytics': analytics,
        }
        return render(request, 'documents/partials/rating_buttons.html', context)
        
    except Document.DoesNotExist:
        return JsonResponse({'error': 'Document not found'}, status=404)


from django.core.signing import TimestampSigner, BadSignature
from django.http import FileResponse, HttpResponseForbidden, HttpResponseNotFound

def serve_download(request, share_id):
    """
    Endpoint for secure, token-gated document downloads.
    """
    token = request.GET.get('t')
    if not token:
        return HttpResponseForbidden("Missing download token.")
        
    signer = TimestampSigner()
    try:
        # Verify token, expires in 24 hours (86400 seconds)
        data = signer.unsign_object(token, max_age=86400)
    except BadSignature:
        return HttpResponseForbidden("Invalid or expired download token.")
        
    try:
        document = Document.objects.get(share_id=share_id)

        # A signed URL is bound to one document. Private files also require
        # the owner session; a forwarded link does not grant access.
        if data != str(document.share_id):
            return HttpResponseForbidden("Download token does not match this document.")
        if (document.is_ai_generated or document.visibility == 'private') and (
            not request.user.is_authenticated or document.uploaded_by_id != request.user.id
        ):
            return HttpResponseNotFound("Document not found.")
        
        from .engagement.models import DocumentDownload
        from .models import DocumentFile
        
        file_id = request.GET.get('file_id')
        document_file = DocumentFile.objects.get(id=file_id, document_version__document=document) if file_id else document.latest_version.files.first()

        if not document_file:
            return HttpResponseNotFound("File not found.")

        if document_file.storage_private and (
            not request.user.is_authenticated or document.uploaded_by_id != request.user.id
        ):
            return HttpResponseNotFound("File not found.")
            
        # Create download record
        DocumentDownload.objects.create(
            document=document,
            document_file=document_file,
            user=request.user if request.user.is_authenticated else None,
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:500]
        )
        
        # Invalidate cache
        from django.core.cache import cache
        cache.delete(f'doc_download_count_{share_id}')
        
        # Stream file securely
        import mimetypes
        content_type, _ = mimetypes.guess_type(document_file.file.name)
        response = FileResponse(document_file.file.open('rb'), content_type=content_type or 'application/octet-stream')
        filename = f"{document.title}.{document_file.extension}"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
        
    except Document.DoesNotExist:
        return HttpResponseNotFound("Document not found")
    except Exception as e:
        return HttpResponseForbidden(f"Download failed: {str(e)}")


@csrf_exempt
@require_POST
@login_required
def share_document(request, share_id):
    """
    Share a document to profile or group via HTMX.
    """
    try:
        document = Document.objects.get(share_id=share_id)
        share_type = request.POST.get('share_type')  # 'profile', 'group', or 'copy_link'
        
        if share_type == 'copy_link':
            # Track the copy link share
            DocumentShare.objects.create(
                document=document,
                user=request.user,
                platform='copy_link'
            )
            
            # Trigger analytics update asynchronously
            from .tasks.processing import update_document_analytics
            update_document_analytics.delay(document.id)
            
            return JsonResponse({
                'success': True,
                'message': 'Link copied to clipboard',
                'url': request.build_absolute_uri(f"/documents/document/{share_id}/")
            })
        
        elif share_type == 'profile':
            # Share to user's profile feed
            from posts.models import Post
            
            course = getattr(request.user, 'course', None)
            doc_thumbnail = None
            if document.latest_version:
                first_file = document.latest_version.files.first()
                if first_file and (first_file.thumbnail_path or first_file.preview_path):
                    doc_thumbnail = first_file.thumbnail_path or first_file.preview_path
            
            post = Post.objects.create(
                author=request.user,
                visibility=request.user.post_privacy,
                course=course,
                gradient_class='none',
                thumbnail=doc_thumbnail,
                content=f"Shared a document: {document.title}",
                shared_document=document
            )
            
            # Track the share
            DocumentShare.objects.create(
                document=document,
                user=request.user,
                platform='profile'
            )
            
            # Trigger analytics update asynchronously
            from .tasks.processing import update_document_analytics
            update_document_analytics.delay(document.id)
            
            return JsonResponse({
                'success': True,
                'message': 'Document shared to your profile'
            })
        
        elif share_type == 'group':
            # Share to a group
            group_id = request.POST.get('group_id')
            if not group_id:
                return JsonResponse({'error': 'Group ID required'}, status=400)
            
            from groups.models import Group, Membership, MembershipStatus
            from posts.models import Post
            
            try:
                group = Group.objects.get(id=group_id)
            except Group.DoesNotExist:
                return JsonResponse({'error': 'Group not found'}, status=404)
            
            # Check if user is an approved member of the group, group creator, or staff
            is_member = Membership.objects.filter(
                group=group,
                user=request.user,
                status=MembershipStatus.APPROVED
            ).exists()
            
            if not is_member and group.created_by != request.user and not request.user.is_staff:
                return JsonResponse({'error': 'Not an approved member of this group'}, status=403)
            
            doc_thumbnail = None
            if document.latest_version:
                first_file = document.latest_version.files.first()
                if first_file and (first_file.thumbnail_path or first_file.preview_path):
                    doc_thumbnail = first_file.thumbnail_path or first_file.preview_path
            
            # Create post in group
            post = Post.objects.create(
                author=request.user,
                visibility=request.user.post_privacy,
                group=group,
                course=group.course,
                gradient_class='none',
                thumbnail=doc_thumbnail,
                content=f"Shared a document: {document.title}",
                shared_document=document
            )
            
            # Track the share
            DocumentShare.objects.create(
                document=document,
                user=request.user,
                platform='group'
            )
            
            # Trigger analytics update asynchronously
            from .tasks.processing import update_document_analytics
            update_document_analytics.delay(document.id)
            
            return JsonResponse({
                'success': True,
                'message': f'Document shared to {group.name}'
            })
        
        else:
            return JsonResponse({'error': 'Invalid share type'}, status=400)
        
    except Document.DoesNotExist:
        return JsonResponse({'error': 'Document not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def user_groups_for_sharing(request):
    """
    Return JSON list of groups the authenticated user is an approved member of.
    """
    from groups.models import Membership, MembershipStatus
    memberships = Membership.objects.filter(
        user=request.user,
        status=MembershipStatus.APPROVED
    ).select_related('group').order_by('group__name')
    
    groups_data = [
        {
            'id': m.group.id,
            'name': m.group.name,
            'description': m.group.description or '',
        }
        for m in memberships
    ]
    return JsonResponse({'groups': groups_data})


@csrf_exempt
@login_required
def document_stats(request, share_id):
    """
    Get cached document statistics via HTMX.
    """
    try:
        document = Document.objects.get(share_id=share_id)
        
        # Get or create analytics
        analytics, _ = DocumentAnalytics.objects.get_or_create(document=document)
        
        # If analytics are stale (older than 5 minutes), trigger update
        from django.utils import timezone
        from datetime import timedelta
        if analytics.last_updated < timezone.now() - timedelta(minutes=5):
            from .tasks.processing import update_document_analytics
            update_document_analytics.delay(document.id)
        
        return JsonResponse({
            'view_count': analytics.view_count,
            'download_count': analytics.download_count,
            'bookmark_count': analytics.bookmark_count,
            'share_count': analytics.share_count,
            'rating_count': analytics.rating_count,
            'positive_rating_count': analytics.positive_rating_count,
            'negative_rating_count': analytics.negative_rating_count,
        })
        
    except Document.DoesNotExist:
        return JsonResponse({'error': 'Document not found'}, status=404)
