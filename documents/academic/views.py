"""
HTMX and API views for academic dropdowns and student unit enrollments.
"""
import json
import logging
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST
from .models import (
    AcademicLevel,
    AcademicUnit,
    AcademicYear,
    Institution,
    Programme,
    ProgrammeTimetable,
    Semester,
    TimetableStatus,
    TimetableType,
)
from .services import StudentAcademicEnrollmentService
from .timetable_service import ProgrammeTimetableManager, compute_free_slots
from .workspace import build_week_grid, today_agenda

logger = logging.getLogger(__name__)

# Centre sections served inside the VASTO Student Dashboard workspace.
_ACADEMIC_SECTIONS = ('timetable', 'overall', 'calendar', 'assignments', 'exams')


def load_academic_levels(request):
    """Load academic levels for a programme (HTMX endpoint)."""
    programme_id = request.GET.get('programme')
    levels = AcademicLevel.objects.filter(is_active=True).order_by('level')
    if programme_id:
        try:
            programme = Programme.objects.filter(id=programme_id).first()
            if programme and programme.duration_years:
                levels = levels.filter(level__lte=programme.duration_years)
        except Exception:
            pass
    
    context = {'levels': levels}
    return render(request, 'users/partials/academic_level_options.html', context)


def load_academic_years(request):
    """Load academic years for an academic level (HTMX endpoint)."""
    level_id = request.GET.get('academic_level')
    years = AcademicYear.objects.all().order_by('-code')
    
    context = {'years': years}
    return render(request, 'users/partials/academic_year_options.html', context)


def load_semesters(request):
    """Load semesters for an academic year (HTMX endpoint)."""
    year_id = request.GET.get('academic_year')
    semesters = Semester.objects.filter(academic_year_id=year_id).order_by('number')
    
    context = {'semesters': semesters}
    return render(request, 'users/partials/semester_options.html', context)


def _serialize_enrollments(enrollments):
    return [
        {
            'id': enr.id,
            'unit_id': enr.academic_unit_id,
            'code': enr.academic_unit.code,
            'name': enr.academic_unit.name,
            'credit_hours': enr.academic_unit.credit_hours,
            'source': enr.source,
            'is_active': enr.is_active,
            'academic_level': enr.academic_level.name if enr.academic_level else None,
            'semester': str(enr.semester) if enr.semester else None,
        }
        for enr in enrollments
    ]


@login_required
@require_GET
def student_units_api(request):
    """Return the authenticated student's active enrolled units (auto-syncing if empty)."""
    enrollments = StudentAcademicEnrollmentService.get_active_enrollments(request.user)
    return JsonResponse({
        'units': _serialize_enrollments(enrollments),
        'count': len(enrollments),
    })


@login_required
@require_POST
def sync_student_units_api(request):
    """Force re-synchronization of the student's units from their Programme + Year + Semester."""
    enrollments = StudentAcademicEnrollmentService.sync_student_units(request.user, force_resync=True)
    return JsonResponse({
        'success': True,
        'units': _serialize_enrollments(enrollments),
        'count': len(enrollments),
    })


@login_required
@require_POST
def enroll_student_unit(request):
    """Add a custom elective or retake AcademicUnit to the student's active enrollments."""
    payload = {}
    if request.content_type and 'application/json' in request.content_type:
        try:
            payload = json.loads(request.body.decode('utf-8') or '{}')
        except ValueError:
            payload = {}
    unit_ident = payload.get('unit_id') or payload.get('code') or request.POST.get('unit_id') or request.POST.get('code')
    source = payload.get('source') or request.POST.get('source') or 'custom_elective'

    if not unit_ident:
        return JsonResponse({'success': False, 'error': 'unit_id or code is required.'}, status=400)

    if str(unit_ident).isdigit():
        unit = AcademicUnit.objects.filter(pk=int(unit_ident), is_active=True).first()
    else:
        unit = AcademicUnit.objects.filter(code__iexact=str(unit_ident).strip(), is_active=True).first()

    if not unit:
        return JsonResponse({'success': False, 'error': 'Academic unit not found.'}, status=404)

    StudentAcademicEnrollmentService.enroll_unit(request.user, unit, source=source)
    enrollments = StudentAcademicEnrollmentService.get_active_enrollments(request.user, auto_sync_if_empty=False)
    return JsonResponse({
        'success': True,
        'units': _serialize_enrollments(enrollments),
        'count': len(enrollments),
    })


@login_required
@require_POST
def unenroll_student_unit(request, unit_id: int):
    """Deactivate a unit enrollment for the authenticated student."""
    removed = StudentAcademicEnrollmentService.unenroll_unit(request.user, unit_id)
    enrollments = StudentAcademicEnrollmentService.get_active_enrollments(request.user, auto_sync_if_empty=False)
    return JsonResponse({
        'success': True,
        'removed': removed,
        'units': _serialize_enrollments(enrollments),
        'count': len(enrollments),
    })


def _parse_request_payload(request) -> dict:
    if request.content_type and 'application/json' in request.content_type:
        try:
            return json.loads(request.body.decode('utf-8') or '{}')
        except ValueError:
            return {}
    return request.POST.dict()


def _build_programme_curriculum_matrix(programme, duration_years: int = 4):
    """Build a structured Year (1..N) x Semester (1..2) unit count and unit list matrix for a programme."""
    from .models import ProgrammeUnit

    max_years = max(4, min(int(duration_years or 4), 6))
    matrix = {}
    for yr in range(1, max_years + 1):
        matrix[yr] = {1: [], 2: []}

    if not programme:
        return matrix

    pu_qs = (
        ProgrammeUnit.objects.filter(
            programme=programme,
            academic_unit__is_active=True,
        )
        .select_related('academic_unit', 'academic_level', 'semester')
        .order_by('academic_unit__code', '-id')
    )

    seen = set()
    for pu in pu_qs:
        lvl_num = pu.academic_level.level if pu.academic_level else 1
        sem_num = pu.semester.number if pu.semester else 1
        if sem_num not in (1, 2):
            sem_num = 1
        if lvl_num not in matrix:
            matrix[lvl_num] = {1: [], 2: []}
        key = (lvl_num, sem_num, pu.academic_unit_id)
        if key in seen:
            continue
        seen.add(key)
        matrix[lvl_num][sem_num].append(pu)

    return matrix


def _enrolled_unit_document_counts(unit_ids):
    """Map unit_id → public ready-document count for the enrolled-units rail list."""
    from django.db.models import Count, Q
    ids = [u for u in (unit_ids or []) if u]
    if not ids:
        return {}
    rows = AcademicUnit.objects.filter(id__in=ids).annotate(
        ready_doc_count=Count(
            'documents',
            filter=Q(
                documents__document__status='ready',
                documents__document__is_available=True,
                documents__document__visibility='public',
            ),
            distinct=True,
        )
    )
    return {row.id: getattr(row, 'ready_doc_count', 0) or 0 for row in rows}


def _workspace_week_grid(active_timetable, summary):
    """Render-ready geometry for the dashboard week grid (None when no data)."""
    if not active_timetable or not getattr(active_timetable, 'is_active', False):
        return None
    summary = summary or {}
    student_level_id = summary.get('academic_level_id')
    student_semester_id = summary.get('semester_id')
    # "Today" chrome only when the viewed timetable belongs to the student's
    # own live cohort — otherwise the grid is a historical/other-cohort view.
    is_current = bool(
        summary.get('programme_id')
        and active_timetable.academic_level_id == student_level_id
        and active_timetable.semester_id == student_semester_id
    )
    return build_week_grid(
        getattr(active_timetable, 'schedule_data', None),
        is_current_context=is_current,
    )


@login_required
@require_GET
def academic_dashboard(request):
    """Student Academic & Units Dashboard (/documents/academic/dashboard/)."""
    user = request.user
    summary = StudentAcademicEnrollmentService.get_student_academic_summary(user)

    student_prog = summary['programme']
    student_lvl = summary['academic_level']
    student_sem_num = summary['semester_number'] or 1

    # Programme is locked to the student's enrolled programme
    selected_programme = student_prog

    req_year = request.GET.get('year') or request.GET.get('level')
    if req_year and str(req_year).isdigit() and 1 <= int(req_year) <= 6:
        selected_year_num = int(req_year)
    elif student_lvl:
        selected_year_num = student_lvl.level
    else:
        selected_year_num = 1

    req_sem = request.GET.get('semester')
    if req_sem and str(req_sem).isdigit() and int(req_sem) in (1, 2):
        selected_sem_num = int(req_sem)
    else:
        selected_sem_num = student_sem_num if student_sem_num in (1, 2) else 1

    selected_level_obj = StudentAcademicEnrollmentService.resolve_or_ensure_academic_level(selected_year_num)
    duration_years = selected_programme.duration_years if selected_programme and selected_programme.duration_years else 4
    year_numbers = list(range(1, max(4, min(duration_years, 6)) + 1))

    # Ensure AcademicLevel rows exist for 1..duration_years
    academic_levels = []
    for yr_num in year_numbers:
        lvl_obj = StudentAcademicEnrollmentService.resolve_or_ensure_academic_level(yr_num)
        if lvl_obj:
            academic_levels.append(lvl_obj)

    curriculum_matrix = _build_programme_curriculum_matrix(selected_programme, duration_years)
    year_tabs = []
    for yr_num in year_numbers:
        sem1_count = len(curriculum_matrix.get(yr_num, {}).get(1, []))
        sem2_count = len(curriculum_matrix.get(yr_num, {}).get(2, []))
        year_tabs.append({
            'year_number': yr_num,
            'label': f'Year {yr_num}',
            'sem1_count': sem1_count,
            'sem2_count': sem2_count,
            'total_count': sem1_count + sem2_count,
            'is_selected': yr_num == selected_year_num,
            'is_student_current': bool(student_lvl and student_lvl.level == yr_num),
        })

    selected_sem1_count = len(curriculum_matrix.get(selected_year_num, {}).get(1, []))
    selected_sem2_count = len(curriculum_matrix.get(selected_year_num, {}).get(2, []))

    context_units = []
    if selected_programme:
        context_units = StudentAcademicEnrollmentService.get_units_for_context(
            programme_id=selected_programme.id,
            academic_level_ident=selected_year_num,
            semester_ident=selected_sem_num,
        )

    programmes = Programme.objects.filter(is_active=True).select_related('department__school').order_by('name')

    active_timetable = None
    if selected_programme and selected_level_obj:
        active_timetable = ProgrammeTimetableManager.get_active_timetable(
            institution=getattr(user, 'institution', None) or (selected_programme.get_institution() if selected_programme else None),
            programme=selected_programme,
            academic_level=selected_level_obj,
            semester=summary.get('semester') or getattr(user, 'semester', None),
        )
    # No silent cross-cohort fallbacks: if this cohort has no published
    # timetable, the section renders its honest empty state (upload CTA).
    # Other cohorts are still browseable deliberately via the year pills.

    week_grid = _workspace_week_grid(active_timetable, summary)
    enrolled_units = summary.get('enrolled_units') or []
    enrolled_doc_counts = _enrolled_unit_document_counts(
        [entry.get('id') for entry in enrolled_units]
    )
    for entry in enrolled_units:
        entry['doc_count'] = enrolled_doc_counts.get(entry.get('id'), 0)

    today_rows = []
    if active_timetable and getattr(active_timetable, 'is_active', False):
        try:
            today_rows = today_agenda(getattr(active_timetable, 'schedule_data', None))
        except Exception:
            logger.warning('[ACADEMIC_DASHBOARD] today_agenda failed', exc_info=True)

    section = request.GET.get('section') or 'timetable'
    if section not in _ACADEMIC_SECTIONS:
        section = 'timetable'
    refresh_ctx = bool(('year' in request.GET or 'semester' in request.GET))

    context = {
        'page_title': 'vasto student dashboard',
        'academic_summary': summary,
        'selected_programme': selected_programme,
        'selected_year_num': selected_year_num,
        'selected_level_obj': selected_level_obj,
        'selected_sem_num': selected_sem_num,
        'selected_sem1_count': selected_sem1_count,
        'selected_sem2_count': selected_sem2_count,
        'year_tabs': year_tabs,
        'context_units': context_units,
        'context_units_count': len(context_units),
        'academic_levels': academic_levels,
        'programmes': programmes,
        'active_timetable': active_timetable,
        'week_grid': week_grid,
        'today_rows': today_rows,
        'enrolled_doc_counts': enrolled_doc_counts,
        'active_section': section,
        'refresh_ctx': refresh_ctx,
        'document_content_partial': 'documents/academic_dashboard.html',
        'show_library_button': False,
        'show_upload_button': False,
    }

    if request.headers.get('HX-Request'):
        if request.GET.get('partial') == 'ctx_body':
            return render(request, 'documents/academic_workspace/ctx_body.html', context)
        return render(request, f'documents/academic_workspace/sections/{section}.html', context)
    return render(request, 'documents/academic_dashboard.html', context)


@login_required
@require_GET
def lookup_academic_unit_api(request):
    """Live lookup for a canonical unit code to detect shared units across programmes immediately."""
    from django.db.models import Q, Count
    from .models import ProgrammeUnit

    raw_code = request.GET.get('code', '').strip()
    is_valid, canonical_code, error_msg = StudentAcademicEnrollmentService.validate_canonical_unit_code(raw_code)
    if not canonical_code:
        return JsonResponse({'found': False, 'canonical_code': '', 'is_valid_format': False})

    compact_code = canonical_code.replace(' ', '')
    unit = (
        AcademicUnit.objects.filter(
            Q(code__iexact=canonical_code) | Q(code__iexact=compact_code),
            is_active=True,
        )
        .annotate(
            ready_doc_count=Count(
                'documents',
                filter=Q(
                    documents__document__status='ready',
                    documents__document__is_available=True,
                    documents__document__visibility='public',
                ),
                distinct=True,
            )
        )
        .first()
    )

    if not unit:
        return JsonResponse({
            'found': False,
            'canonical_code': canonical_code,
            'is_valid_format': is_valid,
            'format_hint': None if is_valid else error_msg,
        })

    shared_pus = (
        ProgrammeUnit.objects.filter(academic_unit=unit)
        .select_related('programme', 'academic_level', 'semester')
        .order_by('programme__code')
    )
    shared_programmes = []
    seen_prog_ids = set()
    for pu in shared_pus:
        if pu.programme_id not in seen_prog_ids:
            seen_prog_ids.add(pu.programme_id)
            shared_programmes.append({
                'id': pu.programme_id,
                'code': pu.programme.code,
                'name': pu.programme.name,
                'level': pu.academic_level.name if pu.academic_level else None,
                'semester': f"Semester {pu.semester.number}" if pu.semester else None,
            })

    return JsonResponse({
        'found': True,
        'is_valid_format': True,
        'unit_id': unit.id,
        'canonical_code': unit.code,
        'name': unit.name,
        'document_count': getattr(unit, 'ready_doc_count', 0) or 0,
        'shared_programmes': shared_programmes,
    })


@login_required
@require_GET
def units_for_context_api(request):
    """Return canonical units for a given (programme, level, semester) context."""
    user = request.user
    summary = StudentAcademicEnrollmentService.get_student_academic_summary(user)

    prog_id = request.GET.get('programme')
    if not prog_id and summary['programme']:
        prog_id = summary['programme'].id

    level_ident = request.GET.get('level') or request.GET.get('academic_level') or request.GET.get('year')
    if not level_ident and summary['academic_level']:
        level_ident = summary['academic_level'].level

    sem_ident = request.GET.get('semester')
    if not sem_ident:
        sem_ident = summary['semester_number'] or 1

    units = []
    if prog_id and str(prog_id).isdigit():
        units = StudentAcademicEnrollmentService.get_units_for_context(
            programme_id=int(prog_id),
            academic_level_ident=level_ident,
            semester_ident=sem_ident,
        )

    return JsonResponse({
        'success': True,
        'programme_id': int(prog_id) if prog_id and str(prog_id).isdigit() else None,
        'level': str(level_ident or ''),
        'semester': str(sem_ident or ''),
        'units': units,
        'count': len(units),
    })


@login_required
@require_POST
def add_programme_unit_api(request):
    """Add one or more canonical units to the student's enrolled programme curriculum for a specific Year and Semester."""
    payload = _parse_request_payload(request)
    user = request.user
    summary = StudentAcademicEnrollmentService.get_student_academic_summary(user)

    # Students are strictly locked to their own enrolled programme
    programme = summary['programme']
    if user.is_staff and (payload.get('programme_id') or payload.get('programme')):
        staff_prog_id = payload.get('programme_id') or payload.get('programme')
        if str(staff_prog_id).isdigit():
            programme = Programme.objects.filter(pk=int(staff_prog_id), is_active=True).first() or programme

    if not programme:
        return JsonResponse({'success': False, 'error': 'No enrolled programme found on your academic profile.'}, status=400)

    level_ident = (
        payload.get('academic_level')
        or payload.get('level')
        or payload.get('year')
        or (summary['academic_level'].level if summary['academic_level'] else 1)
    )
    sem_ident = payload.get('semester') or summary['semester_number'] or 1

    # Support either a batch list of `units: [{code, name, is_elective}]` or a single `{code, name}`
    items = payload.get('units')
    if not isinstance(items, list):
        raw_code = payload.get('code') or request.POST.get('code') or ''
        raw_name = payload.get('name') or request.POST.get('name') or ''
        is_elective = str(payload.get('is_elective', '')).lower() in ('true', '1', 'yes', 'on')
        items = [{'code': raw_code, 'name': raw_name, 'is_elective': is_elective}]

    cleaned_items = [
        item for item in items
        if isinstance(item, dict) and (str(item.get('code') or '').strip() or str(item.get('name') or '').strip())
    ]
    if not cleaned_items:
        return JsonResponse({'success': False, 'error': 'Please provide at least one unit code and unit name.'}, status=400)

    added_units = []
    errors = []
    for idx, item in enumerate(cleaned_items, start=1):
        try:
            unit, pu, unit_created, mapping_created = StudentAcademicEnrollmentService.add_unit_to_programme(
                user=user,
                programme=programme,
                raw_code=item.get('code', ''),
                raw_name=item.get('name', ''),
                academic_level_ident=level_ident,
                semester_ident=sem_ident,
                is_elective=bool(item.get('is_elective', False)),
            )
            added_units.append({
                'id': unit.id,
                'code': unit.code,
                'name': unit.name,
                'unit_created': unit_created,
                'shared_reused': not unit_created,
                'mapping_created': mapping_created,
                'is_elective': pu.is_elective,
            })
        except ValueError as ve:
            prefix = f"Row {idx}: " if len(cleaned_items) > 1 else ""
            errors.append(f"{prefix}{str(ve)}")
        except Exception as exc:
            prefix = f"Row {idx}: " if len(cleaned_items) > 1 else ""
            errors.append(f"{prefix}Could not save unit ({str(exc)}).")

    if errors and not added_units:
        return JsonResponse({'success': False, 'error': ' '.join(errors), 'errors': errors}, status=400)

    updated_units = StudentAcademicEnrollmentService.get_units_for_context(
        programme_id=programme.id,
        academic_level_ident=level_ident,
        semester_ident=sem_ident,
    )
    updated_summary = StudentAcademicEnrollmentService.get_student_academic_summary(user)

    return JsonResponse({
        'success': True,
        'added_units': added_units,
        'warnings': errors,
        'units': updated_units,
        'count': len(updated_units),
        'enrolled_count': updated_summary['enrolled_count'],
    })


@login_required
@require_POST
def update_academic_profile_api(request):
    """Academic programme, department, year, and active semester are locked to the student record and system timetable."""
    if not request.user.is_staff:
        return JsonResponse({
            'success': False,
            'error': 'Programme, department, year of study, and active semester are managed by your academic record and the system timetable.'
        }, status=403)

    payload = _parse_request_payload(request)
    programme_id = payload.get('programme_id') or payload.get('programme')
    level_ident = payload.get('academic_level') or payload.get('level') or payload.get('year')
    sem_ident = payload.get('semester')

    try:
        summary = StudentAcademicEnrollmentService.update_student_academic_profile(
            user=request.user,
            programme_id=programme_id,
            academic_level_ident=level_ident,
            semester_ident=sem_ident,
        )
    except ValueError as ve:
        return JsonResponse({'success': False, 'error': str(ve)}, status=400)

    return JsonResponse({
        'success': True,
        'programme': {
            'id': summary['programme'].id,
            'code': summary['programme'].code,
            'name': summary['programme'].name,
        } if summary['programme'] else None,
        'school': summary['school'].name if summary['school'] else None,
        'department': summary['department'].name if summary['department'] else None,
        'academic_level': {
            'id': summary['academic_level'].id,
            'level': summary['academic_level'].level,
            'name': summary['academic_level'].name,
        } if summary['academic_level'] else None,
        'semester_number': summary['semester_number'],
        'enrolled_count': summary['enrolled_count'],
    })


@login_required
@require_POST
def upload_timetable_view(request):
    """
    Handle student timetable image upload (from fullscreen crop composer).

    Phase 1 (this view): Validates the upload, saves the image file, creates a
    ProgrammeTimetable record at status=PROCESSING, fires the Celery extraction
    task, and responds immediately — the student never waits for AI.

    Phase 2 (Celery task): process_timetable_image_task calls the configured
    local vision provider,
    populates schedule_data, and marks the record ACTIVE.
    """
    user = request.user
    image_file = request.FILES.get('image') or request.FILES.get('timetable_image')
    if not image_file:
        return JsonResponse({'success': False, 'error': 'No timetable image file provided.'}, status=400)

    # ── Resolve programme and academic level ──────────────────────────────────
    prog_id = request.POST.get('programme_id') or request.POST.get('programme')
    level_id = request.POST.get('academic_level') or request.POST.get('level') or request.POST.get('year')
    sem_id = request.POST.get('semester')
    change_summary = request.POST.get('change_summary', '').strip()

    programme = None
    if prog_id and str(prog_id).isdigit():
        programme = Programme.objects.filter(pk=int(prog_id), is_active=True).first()
    if not programme:
        programme = getattr(user, 'programme', None)
    if not programme:
        return JsonResponse(
            {'success': False, 'error': 'Please set your academic programme before uploading a timetable.'},
            status=400,
        )

    academic_level = None
    if level_id and str(level_id).isdigit():
        academic_level = StudentAcademicEnrollmentService.resolve_or_ensure_academic_level(int(level_id))
    if not academic_level:
        academic_level = getattr(user, 'academic_level', None) or \
            StudentAcademicEnrollmentService.resolve_or_ensure_academic_level(1)

    semester = None
    if sem_id and str(sem_id).isdigit():
        semester = Semester.objects.filter(pk=int(sem_id)).first()
    if not semester:
        semester = getattr(user, 'semester', None)

    institution = getattr(user, 'institution', None)
    if not institution and programme:
        institution = programme.get_institution()
    if not institution:
        institution = Institution.objects.filter(is_active=True).first()

    # ── Save image + create PROCESSING record + fire task ────────────────────
    try:
        timetable = ProgrammeTimetableManager.save_timetable_image(
            user=user,
            image_file=image_file,
            institution=institution,
            programme=programme,
            academic_level=academic_level,
            semester=semester,
            academic_year=getattr(user, 'academic_year', None),
            change_summary=change_summary,
        )
    except Exception as exc:
        logger.error('[TIMETABLE_UPLOAD] Error saving timetable: %s', exc, exc_info=True)
        return JsonResponse({'success': False, 'error': f'Failed to save timetable: {str(exc)}'}, status=500)

    # HTMX path: render the timetable panel immediately in "processing" state
    if request.headers.get('HX-Request'):
        summary = StudentAcademicEnrollmentService.get_student_academic_summary(user)
        context = {
            'active_timetable': timetable,
            'academic_summary': summary,
            'selected_programme': programme,
            'selected_level_obj': academic_level,
            'selected_year_num': academic_level.level if academic_level else (summary.get('academic_level_number') or 1),
            'selected_sem_num': semester.number if semester else (summary.get('semester_number') or 1),
            'week_grid': _workspace_week_grid(timetable, summary),
        }
        return render(request, 'documents/academic_workspace/sections/timetable.html', context)

    return JsonResponse({
        'success': True,
        'processing': timetable.status == TimetableStatus.PROCESSING,
        'message': 'Timetable uploaded — Spark AI is now extracting the schedule in the background.',
        'timetable_id': timetable.id,
        'revision_number': timetable.revision_number,
        'title': timetable.title,
        'status': timetable.status,
    })


@login_required
@require_GET
def timetable_status_api(request, timetable_id: int):
    """
    Polling endpoint used by the dashboard timetable section (academic_workspace)

    Returns the current timetable status.  When status is ACTIVE the full
    schedule_data is included so the client can update the panel without a
    page refresh.  When status is PROCESSING the client should poll again.
    When status is FAILED the client should surface an error and offer a retry.
    """
    timetable = ProgrammeTimetable.objects.filter(pk=timetable_id).first()
    if not timetable:
        return JsonResponse({'found': False, 'status': 'not_found'}, status=404)

    payload = {
        'found': True,
        'timetable_id': timetable.id,
        'status': timetable.status,
        'is_active': timetable.is_active,
        'revision_number': timetable.revision_number,
        'title': timetable.title,
    }

    if timetable.status == TimetableStatus.ACTIVE and timetable.is_active:
        payload['schedule_data'] = timetable.schedule_data

    # If HTMX request, render the timetable template for ALL states (PROCESSING, ACTIVE, FAILED).
    # This keeps the processing spinner active while background AI extraction runs,
    # and prevents raw JSON from leaking into the DOM.
    if request.headers.get('HX-Request'):
        summary = StudentAcademicEnrollmentService.get_student_academic_summary(request.user)
        selected_year_num = timetable.academic_level.level if timetable.academic_level else (summary.get('academic_level_number') or 1)
        selected_sem_num = timetable.semester.number if timetable.semester else (summary.get('semester_number') or 1)
        context = {
            'active_timetable': timetable,
            'academic_summary': summary,
            'selected_programme': timetable.programme,
            'selected_level_obj': timetable.academic_level,
            'selected_year_num': selected_year_num,
            'selected_sem_num': selected_sem_num,
            'week_grid': _workspace_week_grid(timetable, summary),
        }
        return render(request, 'documents/academic_workspace/sections/timetable.html', context)

    return JsonResponse(payload)


@login_required
@require_GET
def timetable_active_api(request):
    """Return active timetable and free slots for the student's cohort."""
    user = request.user
    prog_id = request.GET.get('programme_id')
    level_id = request.GET.get('year') or request.GET.get('level')

    programme = Programme.objects.filter(pk=int(prog_id)).first() if (prog_id and str(prog_id).isdigit()) else getattr(user, 'programme', None)
    academic_level = StudentAcademicEnrollmentService.resolve_or_ensure_academic_level(int(level_id)) if (level_id and str(level_id).isdigit()) else getattr(user, 'academic_level', None)

    if not programme or not academic_level:
        return JsonResponse({'active': False, 'timetable': None, 'message': 'Cohort not fully specified.'})

    timetable = ProgrammeTimetableManager.get_active_timetable(
        institution=getattr(user, 'institution', None),
        programme=programme,
        academic_level=academic_level,
        semester=getattr(user, 'semester', None),
    )

    if not timetable:
        return JsonResponse({'active': False, 'timetable': None, 'message': 'No active timetable found for this cohort.'})

    return JsonResponse({
        'active': True,
        'timetable': {
            'id': timetable.id,
            'title': timetable.title,
            'revision_number': timetable.revision_number,
            'programme': timetable.programme.code if timetable.programme else None,
            'academic_level': timetable.academic_level.name if timetable.academic_level else None,
            'semester': str(timetable.semester) if timetable.semester else None,
            'schedule_data': timetable.schedule_data,
            'updated_at': timetable.updated_at.isoformat(),
        }
    })


@login_required
@require_POST
def timetable_slot_mutation_view(request, timetable_id: int):
    """Add, edit, or delete a lecture slot in an existing timetable."""
    user = request.user
    timetable = ProgrammeTimetable.objects.filter(pk=timetable_id).first()
    if not timetable:
        return JsonResponse({'success': False, 'error': 'Timetable not found.'}, status=404)

    payload = _parse_request_payload(request)
    action = payload.get('action', 'update').lower().strip()
    slot_id = payload.get('slot_id') or payload.get('id')
    slot_data = payload.get('slot') or payload

    try:
        updated_tt = ProgrammeTimetableManager.mutate_timetable_slot(
            timetable=timetable,
            action=action,
            slot_data=slot_data if isinstance(slot_data, dict) else None,
            slot_id=slot_id,
            user=user,
        )
    except Exception as exc:
        logger.error(f"[TIMETABLE_MUTATE] Error mutating slot: {exc}", exc_info=True)
        return JsonResponse({'success': False, 'error': str(exc)}, status=400)

    if request.headers.get('HX-Request'):
        summary = StudentAcademicEnrollmentService.get_student_academic_summary(user)
        context = {
            'active_timetable': updated_tt,
            'academic_summary': summary,
            'selected_programme': updated_tt.programme,
            'selected_level_obj': updated_tt.academic_level,
            'week_grid': _workspace_week_grid(updated_tt, summary),
        }
        return render(request, 'documents/academic_workspace/sections/timetable.html', context)

    return JsonResponse({
        'success': True,
        'message': f"Timetable slot {action}d successfully.",
        'schedule_data': updated_tt.schedule_data,
    })


