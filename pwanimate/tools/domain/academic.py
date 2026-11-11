"""
Academic Lookup Tool for Pwanimate.

Allows deterministic, read-only querying of Pwani University academic hierarchy:
Schools, Departments, Programmes, and Courses.
"""

from typing import Any, Dict, List, Optional
from django.db.models import Q

from pwanimate.tools.base import BaseDomainTool, ToolResult
from pwanimate.tools.exceptions import ToolValidationError
from courses.models import School, Department, Programme, Course


class AcademicLookupTool(BaseDomainTool):
    """
    Look up schools, departments, programmes, or courses within Pwani University.
    """
    name = "academic_lookup"
    description = (
        "Look up Pwani University academic structural metadata including "
        "Schools, Departments, Programmes, and Courses."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "entity_type": {
                "type": "string",
                "enum": ["school", "department", "programme", "course", "unit", "my_units", "all"],
                "description": "The tier of academic hierarchy to query (default: all)",
                "default": "all",
            },
            "query": {
                "type": "string",
                "description": "Search term matching code or title",
            },
            "code": {
                "type": "string",
                "description": "Exact code match (e.g. 'SPAS', 'COMP', 'BSC-CS', 'CSC221')",
            },
            "parent_id": {
                "type": "integer",
                "description": "Optional parent ID filter (e.g. school_id for departments, department_id for programmes)",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return (max 20)",
                "default": 10,
            },
        },
        "required": [],
    }

    def execute(
        self,
        user: Any,
        entity_type: str = "all",
        query: Optional[str] = None,
        code: Optional[str] = None,
        parent_id: Optional[int] = None,
        limit: int = 10,
        **kwargs,
    ) -> ToolResult:
        limit = min(max(1, int(limit)), 20)
        entity_type = (entity_type or "all").lower().strip()

        results: List[Dict[str, Any]] = []

        # 0. Student's Active Enrolled Units (when entity_type is 'my_units' or 'unit' or 'all')
        if user and getattr(user, "is_authenticated", False) and entity_type in ("my_units", "unit", "all"):
            try:
                from documents.academic.services import StudentAcademicEnrollmentService
                enrollments = StudentAcademicEnrollmentService.get_active_enrollments(user)
                for enr in enrollments:
                    unit = enr.academic_unit
                    if code and unit.code.lower() != code.lower():
                        continue
                    if query and query.lower() not in unit.code.lower() and query.lower() not in unit.name.lower():
                        if entity_type != "my_units":
                            continue
                    results.append({
                        "entity_type": "enrolled_unit",
                        "id": unit.id,
                        "code": unit.code,
                        "name": unit.name,
                        "credit_hours": unit.credit_hours,
                        "enrollment_source": enr.source,
                        "academic_level": enr.academic_level.name if enr.academic_level else None,
                        "semester": str(enr.semester) if enr.semester else None,
                    })
                    if len(results) >= limit:
                        break
            except Exception:
                pass

        # 1. Schools
        if len(results) < limit and entity_type in ("school", "all"):
            qs = School.objects.filter(is_active=True)
            if code:
                qs = qs.filter(code__iexact=code)
            if query:
                qs = qs.filter(Q(name__icontains=query) | Q(code__icontains=query))
            for item in qs[: limit - len(results)]:
                results.append({
                    "entity_type": "school",
                    "id": item.id,
                    "code": item.code,
                    "name": item.name,
                    "slug": item.slug,
                    "description": item.description or "",
                })
                if len(results) >= limit:
                    break

        # 2. Departments
        if len(results) < limit and entity_type in ("department", "all"):
            qs = Department.objects.filter(is_active=True).select_related("school")
            if code:
                qs = qs.filter(code__iexact=code)
            if parent_id:
                qs = qs.filter(school_id=parent_id)
            if query:
                qs = qs.filter(Q(name__icontains=query) | Q(code__icontains=query))
            for item in qs[: limit - len(results)]:
                results.append({
                    "entity_type": "department",
                    "id": item.id,
                    "code": item.code,
                    "name": item.name,
                    "slug": item.slug,
                    "school_code": item.school.code if item.school else None,
                    "school_name": item.school.name if item.school else None,
                    "description": item.description or "",
                })
                if len(results) >= limit:
                    break

        # 3. Programmes
        if len(results) < limit and entity_type in ("programme", "all"):
            qs = Programme.objects.filter(is_active=True).select_related("school", "department")
            if code:
                qs = qs.filter(code__iexact=code)
            if parent_id:
                qs = qs.filter(Q(department_id=parent_id) | Q(school_id=parent_id))
            if query:
                qs = qs.filter(Q(name__icontains=query) | Q(code__icontains=query))
            for item in qs[: limit - len(results)]:
                results.append({
                    "entity_type": "programme",
                    "id": item.id,
                    "code": item.code,
                    "name": item.name,
                    "slug": item.slug,
                    "academic_level": item.academic_level,
                    "duration_years": item.duration_years,
                    "school_code": item.school.code if item.school else None,
                    "department_name": item.department.name if item.department else None,
                    "description": item.description or "",
                })
                if len(results) >= limit:
                    break

        # 4. Academic Units (from documents.academic.models)
        if len(results) < limit and entity_type in ("unit", "course", "all"):
            try:
                from documents.academic.models import AcademicUnit
                u_qs = AcademicUnit.objects.filter(is_active=True)
                if code:
                    u_qs = u_qs.filter(code__iexact=code)
                if query:
                    u_qs = u_qs.filter(Q(name__icontains=query) | Q(code__icontains=query))
                seen_codes = {r.get("code") for r in results if r.get("entity_type") == "enrolled_unit"}
                for u_item in u_qs[: limit - len(results)]:
                    if u_item.code in seen_codes:
                        continue
                    results.append({
                        "entity_type": "academic_unit",
                        "id": u_item.id,
                        "code": u_item.code,
                        "name": u_item.name,
                        "slug": u_item.slug,
                        "credit_hours": u_item.credit_hours,
                        "description": u_item.description or "",
                    })
                    if len(results) >= limit:
                        break
            except Exception:
                pass

        # 5. Courses (legacy courses.models)
        if len(results) < limit and entity_type in ("course", "all"):
            qs = Course.objects.filter(is_active=True).select_related("programme", "department", "school")
            if code:
                qs = qs.filter(code__iexact=code)
            if parent_id:
                qs = qs.filter(Q(programme_id=parent_id) | Q(department_id=parent_id) | Q(school_id=parent_id))
            if query:
                qs = qs.filter(Q(name__icontains=query) | Q(code__icontains=query))
            for item in qs[: limit - len(results)]:
                results.append({
                    "entity_type": "course",
                    "id": item.id,
                    "code": item.code or "",
                    "name": item.name,
                    "slug": item.slug or "",
                    "degree_type": item.degree_type or "",
                    "programme_code": item.programme.code if item.programme else None,
                    "school_code": item.school.code if item.school else None,
                    "department_name": item.department.name if item.department else None,
                    "description": item.description or "",
                })
                if len(results) >= limit:
                    break

        return ToolResult.ok(
            results,
            count=len(results),
            query=query,
            entity_type=entity_type,
        )


class StudentTimetableTool(BaseDomainTool):
    """
    Look up current student cohort weekly timetable, lectures for a day,
    or free study slots for revision planning.
    """
    name = "student_timetable"
    description = (
        "Look up the student's cohort lecture timetable, today's classes, "
        "or computed free slots for study revision and schedule planning."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["get_today_schedule", "get_weekly_schedule", "find_free_slots", "get_classes_on_day"],
                "description": "The timetable operation to perform (default: get_today_schedule)",
                "default": "get_today_schedule",
            },
            "day": {
                "type": "string",
                "description": "Specific day of the week (e.g. 'Monday', 'Friday')",
            },
        },
        "required": [],
    }

    def execute(
        self,
        user: Any,
        action: str = "get_today_schedule",
        day: Optional[str] = None,
        **kwargs,
    ) -> ToolResult:
        if not user or not getattr(user, "is_authenticated", False):
            return ToolResult.error("Authentication required to view student cohort timetable.")

        programme = getattr(user, "programme", None)
        academic_level = getattr(user, "academic_level", None)

        if not programme or not academic_level:
            return ToolResult.error(
                "Your academic programme or level is not yet set. "
                "Please configure them in your Academic Dashboard."
            )

        try:
            from documents.academic.timetable_service import ProgrammeTimetableManager
            from documents.academic.models import Institution
            from datetime import datetime

            inst = getattr(user, "institution", None) or Institution.objects.filter(is_active=True).first()
            timetable = ProgrammeTimetableManager.get_active_timetable(
                institution=inst,
                programme=programme,
                academic_level=academic_level,
                semester=getattr(user, "semester", None),
                academic_year=getattr(user, "academic_year", None),
                include_processing=False,
            )

            if not timetable or not timetable.schedule_data:
                return ToolResult.ok({
                    "has_timetable": False,
                    "message": f"No active timetable found for {programme.code} ({academic_level.name}).",
                    "programme": programme.code,
                    "academic_level": academic_level.name,
                })

            sched_data = timetable.schedule_data
            weekly_schedule = sched_data.get("weekly_schedule", [])
            free_slots = sched_data.get("free_slots", [])

            action = (action or "get_today_schedule").lower().strip()

            if action == "get_today_schedule":
                from django.utils import timezone
                today_name = timezone.localdate().strftime("%A")
                today_classes = [s for s in weekly_schedule if s.get("day", "").lower() == today_name.lower()]
                today_free = [f for f in free_slots if f.get("day", "").lower() == today_name.lower()]

                return ToolResult.ok({
                    "has_timetable": True,
                    "day": today_name,
                    "classes_count": len(today_classes),
                    "classes": today_classes,
                    "free_slots": today_free,
                    "revision_number": timetable.revision_number,
                })

            elif action == "get_classes_on_day":
                target_day = (day or datetime.now().strftime("%A")).capitalize()
                day_classes = [s for s in weekly_schedule if s.get("day", "").lower() == target_day.lower()]
                day_free = [f for f in free_slots if f.get("day", "").lower() == target_day.lower()]

                return ToolResult.ok({
                    "has_timetable": True,
                    "day": target_day,
                    "classes_count": len(day_classes),
                    "classes": day_classes,
                    "free_slots": day_free,
                })

            elif action == "find_free_slots":
                if day:
                    target_day = day.capitalize()
                    matched_free = [f for f in free_slots if f.get("day", "").lower() == target_day.lower()]
                else:
                    target_day = "All Week"
                    matched_free = free_slots

                return ToolResult.ok({
                    "has_timetable": True,
                    "day": target_day,
                    "free_slots_count": len(matched_free),
                    "free_slots": matched_free,
                })

            else:  # get_weekly_schedule
                return ToolResult.ok({
                    "has_timetable": True,
                    "programme": programme.code,
                    "academic_level": academic_level.name,
                    "revision_number": timetable.revision_number,
                    "weekly_schedule": weekly_schedule,
                    "free_slots": free_slots,
                })

        except Exception as exc:
            return ToolResult.error(f"Error retrieving timetable: {str(exc)}")

