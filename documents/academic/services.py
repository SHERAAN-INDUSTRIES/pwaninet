"""Services for Academic Graph & Student-to-Unit Linkage.

Provides:
1. StudentAcademicEnrollmentService: Hybrid auto-sync from ProgrammeUnit
   (based on student's programme, academic_level, and semester) + custom
   elective/retake unit management.
2. DocumentAcademicLinkService: Resilient linking of Document -> AcademicUnit
   with automatic inference of missing level/semester/year metadata and
   immediate search index synchronization.
"""

import logging
from typing import List, Optional, Tuple
from django.db import transaction
from django.db.models import Q

from .models import (
    AcademicLevel,
    AcademicUnit,
    AcademicYear,
    Programme,
    ProgrammeUnit,
    Semester,
    StudentUnitEnrollment,
)

logger = logging.getLogger(__name__)


import re
from datetime import date
from django.db.models import Count


class StudentAcademicEnrollmentService:
    """Manages a student's active AcademicUnit enrollments and crowdsourced ProgrammeUnit curriculum."""

    CANONICAL_UNIT_CODE_REGEX = re.compile(r"^[A-Z]{3} [A-Z]\d{3}$")
    COMPACT_UNIT_CODE_REGEX = re.compile(r"^([A-Z]{3})([A-Z]\d{3})$")

    @classmethod
    def normalize_unit_code(cls, raw_code: str) -> str:
        """Normalize raw unit code input (e.g. 'scs b121', 'SCSB121', 'SCS-B121') to canonical 'SCS B121'."""
        if not raw_code:
            return ""
        compact = re.sub(r"[\s\-_\.]+", "", str(raw_code)).upper()
        m = cls.COMPACT_UNIT_CODE_REGEX.match(compact)
        if m:
            return f"{m.group(1)} {m.group(2)}"
        return " ".join(str(raw_code).strip().upper().split())

    @classmethod
    def validate_canonical_unit_code(cls, raw_code: str) -> Tuple[bool, str, str]:
        """Validate and return (is_valid, canonical_code, error_message)."""
        code = cls.normalize_unit_code(raw_code)
        if not code:
            return False, "", "Unit code is required."
        if not cls.CANONICAL_UNIT_CODE_REGEX.match(code):
            return (
                False,
                code,
                "Unit code must follow the canonical format: 3 letters, a space, then 1 letter and 3 digits (e.g., SCS B121).",
            )
        return True, code, ""

    @classmethod
    def resolve_or_ensure_academic_level(cls, level_ident) -> Optional[AcademicLevel]:
        """Resolve an AcademicLevel by instance, PK, level number (1..8), or name."""
        if not level_ident:
            return None
        if isinstance(level_ident, AcademicLevel):
            return level_ident

        val_str = str(level_ident).strip()
        if val_str.lower().startswith("level_"):
            level_num_str = val_str.split("_", 1)[1]
            if level_num_str.isdigit():
                level_num = int(level_num_str)
                level_obj = AcademicLevel.objects.filter(level=level_num).first()
                if not level_obj and 1 <= level_num <= 6:
                    level_obj, _ = AcademicLevel.objects.get_or_create(
                        level=level_num,
                        defaults={"name": f"Year {level_num}", "is_active": True},
                    )
                return level_obj

        if val_str.isdigit():
            num = int(val_str)
            by_pk = AcademicLevel.objects.filter(pk=num).first()
            if by_pk:
                return by_pk
            by_lvl = AcademicLevel.objects.filter(level=num).first()
            if by_lvl:
                return by_lvl
            if 1 <= num <= 6:
                level_obj, _ = AcademicLevel.objects.get_or_create(
                    level=num,
                    defaults={"name": f"Year {num}", "is_active": True},
                )
                return level_obj

        m = re.search(r"(\d+)", val_str)
        if m:
            num = int(m.group(1))
            by_lvl = AcademicLevel.objects.filter(level=num).first()
            if by_lvl:
                return by_lvl
            if 1 <= num <= 6:
                level_obj, _ = AcademicLevel.objects.get_or_create(
                    level=num,
                    defaults={"name": f"Year {num}", "is_active": True},
                )
                return level_obj

        return AcademicLevel.objects.filter(name__iexact=val_str).first()

    @classmethod
    def resolve_or_ensure_semester(
        cls,
        semester_ident,
        academic_year: Optional[AcademicYear] = None,
    ) -> Optional[Semester]:
        """Resolve a Semester by instance, PK, or semester number (1, 2, 3), ensuring one exists if needed."""
        if not semester_ident:
            return None
        if isinstance(semester_ident, Semester):
            return semester_ident

        val_str = str(semester_ident).strip().lower()
        target_number = None

        if val_str in ("1", "2", "3", "sem_1", "sem_2", "sem_3", "semester 1", "semester 2", "semester 3"):
            m = re.search(r"([123])", val_str)
            if m:
                target_number = int(m.group(1))
        elif val_str.isdigit():
            pk_val = int(val_str)
            sem_by_pk = Semester.objects.select_related("academic_year").filter(pk=pk_val).first()
            if sem_by_pk:
                return sem_by_pk
            if pk_val in (1, 2, 3):
                target_number = pk_val

        if target_number in (1, 2, 3):
            if academic_year:
                sem = Semester.objects.select_related("academic_year").filter(
                    number=target_number, academic_year=academic_year
                ).first()
                if sem:
                    return sem

            # Prefer semester belonging to current academic year, or any existing semester with that number
            current_sem = (
                Semester.objects.select_related("academic_year")
                .filter(number=target_number, academic_year__is_current=True)
                .first()
                or Semester.objects.select_related("academic_year")
                .filter(number=target_number, is_current=True)
                .first()
                or Semester.objects.select_related("academic_year")
                .filter(number=target_number)
                .order_by("-academic_year__start_date", "-id")
                .first()
            )
            if current_sem:
                return current_sem

            # Ensure an AcademicYear exists so we can create the Semester record cleanly
            year_obj = (
                academic_year
                or AcademicYear.objects.filter(is_current=True).first()
                or AcademicYear.objects.order_by("-start_date", "-id").first()
            )
            if not year_obj:
                today = date.today()
                yr_code = f"{today.year}/{today.year + 1}"
                year_obj, _ = AcademicYear.objects.get_or_create(
                    code=yr_code,
                    defaults={
                        "name": yr_code,
                        "start_date": date(today.year, 9, 1),
                        "end_date": date(today.year + 1, 6, 30),
                        "is_current": True,
                    },
                )
            sem, _ = Semester.objects.get_or_create(
                number=target_number,
                academic_year=year_obj,
                defaults={
                    "start_date": year_obj.start_date,
                    "end_date": year_obj.end_date,
                    "is_current": (target_number == 1 and not Semester.objects.filter(is_current=True).exists()),
                },
            )
            return sem

        return None

    @classmethod
    def resolve_student_programme_and_level(
        cls, user
    ) -> Tuple[Optional[Programme], Optional[AcademicLevel], Optional[Semester], Optional[AcademicYear]]:
        """Resolve the student's Programme, AcademicLevel, Semester, and AcademicYear.

        Supports both the primary academic fields on User (`programme`, `academic_level`,
        `semester`, `academic_year`) and legacy fallbacks (`course`, `year`).
        """
        if not user or not getattr(user, "is_authenticated", False):
            return None, None, None, None

        programme = getattr(user, "programme", None)
        if not programme and getattr(user, "course_id", None):
            course = getattr(user, "course", None)
            if course:
                course_code = getattr(course, "code", "") or ""
                course_name = getattr(course, "name", "") or ""
                q_filter = Q(name__iexact=course_name) | Q(code__iexact=course_name)
                if course_code:
                    q_filter |= Q(code__iexact=course_code)
                if course_name:
                    q_filter |= Q(name__icontains=course_name)
                programme = (
                    Programme.objects.select_related("department__school__faculty")
                    .filter(q_filter)
                    .first()
                )

        academic_level = getattr(user, "academic_level", None)
        if not academic_level and getattr(user, "year_id", None):
            year_obj = getattr(user, "year", None)
            if year_obj and getattr(year_obj, "level", None):
                academic_level = cls.resolve_or_ensure_academic_level(year_obj.level)

        # Active semester comes from the system timetable (Semester.is_current=True),
        # falling back to user.semester or Semester 1 if none is marked current.
        system_current_sem = (
            Semester.objects.filter(is_current=True).select_related("academic_year").first()
        )
        semester = system_current_sem or getattr(user, "semester", None) or cls.resolve_or_ensure_semester(1)

        academic_year = (
            (semester.academic_year if semester and getattr(semester, "academic_year_id", None) else None)
            or AcademicYear.objects.filter(is_current=True).first()
            or getattr(user, "academic_year", None)
        )

        return programme, academic_level, semester, academic_year

    @classmethod
    def _resolve_programme_units(
        cls,
        programme: Optional[Programme],
        academic_level: Optional[AcademicLevel],
        semester: Optional[Semester],
    ) -> List[ProgrammeUnit]:
        """Return the ordered list of ProgrammeUnit records matching the student's curriculum."""
        if not programme:
            return []

        base_pu_qs = ProgrammeUnit.objects.filter(
            programme=programme,
            academic_unit__is_active=True,
        ).select_related("academic_unit", "academic_level", "semester", "academic_year")

        matched_pu_qs = base_pu_qs
        if academic_level:
            matched_pu_qs = matched_pu_qs.filter(
                Q(academic_level=academic_level) | Q(academic_level__level=academic_level.level)
            )
        if semester:
            sem_q = Q(semester=semester)
            if getattr(semester, "number", None):
                sem_q |= Q(semester__number=semester.number)
            matched_pu_qs = matched_pu_qs.filter(sem_q)

        # Deduplicate by academic_unit_id in case multiple academic_year rows exist
        seen_ids = set()
        results: List[ProgrammeUnit] = []
        for pu in matched_pu_qs.order_by("academic_unit__code", "-id"):
            if pu.academic_unit_id not in seen_ids:
                seen_ids.add(pu.academic_unit_id)
                results.append(pu)
        return results

    @classmethod
    def _build_fallback_enrollments(cls, user) -> List[StudentUnitEnrollment]:
        """Build unsaved StudentUnitEnrollment objects directly from ProgrammeUnit if table is unavailable."""
        programme, academic_level, semester, academic_year = cls.resolve_student_programme_and_level(user)
        if not programme:
            return []
        programme_units = cls._resolve_programme_units(programme, academic_level, semester)
        seen_unit_ids = set()
        fallback_list: List[StudentUnitEnrollment] = []
        for pu in programme_units:
            if pu.academic_unit_id in seen_unit_ids:
                continue
            seen_unit_ids.add(pu.academic_unit_id)
            enr = StudentUnitEnrollment(
                user=user,
                academic_unit=pu.academic_unit,
                academic_level=pu.academic_level or academic_level,
                semester=pu.semester or semester,
                academic_year=pu.academic_year or academic_year,
                source="auto_programme",
                is_active=True,
            )
            enr.academic_unit_id = pu.academic_unit_id
            fallback_list.append(enr)
        return fallback_list

    @classmethod
    def sync_student_units(
        cls,
        user,
        force_resync: bool = False,
    ) -> List[StudentUnitEnrollment]:
        """Synchronize a student's `auto_programme` unit enrollments from `ProgrammeUnit`.

        - Automatically links units offered for the student's Programme + AcademicLevel
          (and Semester when matched).
        - Preserves custom student additions (`custom_elective`, `retake`).
        - Respects explicit student deactivations unless `force_resync=True`.
        """
        if not user or not getattr(user, "is_authenticated", False):
            return []

        programme, academic_level, semester, academic_year = cls.resolve_student_programme_and_level(user)
        try:
            if not programme:
                result = list(
                    StudentUnitEnrollment.objects.filter(user=user, is_active=True)
                    .select_related("academic_unit", "academic_level", "semester", "academic_year")
                )
                setattr(user, "_active_unit_enrollments_cache", result)
                return result

            programme_units = cls._resolve_programme_units(programme, academic_level, semester)
            target_unit_map = {pu.academic_unit_id: pu for pu in programme_units}

            with transaction.atomic():
                existing_enrollments = {
                    enr.academic_unit_id: enr
                    for enr in StudentUnitEnrollment.objects.filter(user=user).select_related(
                        "academic_unit", "academic_level", "semester", "academic_year"
                    )
                }

                # Deactivate stale auto_programme enrollments that are no longer in the student's active curriculum
                for unit_id, enr in existing_enrollments.items():
                    if enr.source == "auto_programme" and unit_id not in target_unit_map and enr.is_active:
                        enr.is_active = False
                        enr.save(update_fields=["is_active", "updated_at"])

                # Create or update target programme units
                for unit_id, pu in target_unit_map.items():
                    existing = existing_enrollments.get(unit_id)
                    resolved_level = academic_level or pu.academic_level
                    resolved_sem = semester or pu.semester
                    resolved_year = academic_year or pu.academic_year

                    if existing is None:
                        StudentUnitEnrollment.objects.create(
                            user=user,
                            academic_unit=pu.academic_unit,
                            academic_level=resolved_level,
                            semester=resolved_sem,
                            academic_year=resolved_year,
                            source="auto_programme",
                            is_active=True,
                        )
                    else:
                        update_fields = []
                        if (force_resync or existing.academic_level_id != (resolved_level.id if resolved_level else None) or existing.semester_id != (resolved_sem.id if resolved_sem else None)) and not existing.is_active:
                            existing.is_active = True
                            update_fields.append("is_active")
                        if existing.academic_level_id != (resolved_level.id if resolved_level else None):
                            existing.academic_level = resolved_level
                            update_fields.append("academic_level")
                        if existing.semester_id != (resolved_sem.id if resolved_sem else None):
                            existing.semester = resolved_sem
                            update_fields.append("semester")
                        if existing.academic_year_id != (resolved_year.id if resolved_year else None):
                            existing.academic_year = resolved_year
                            update_fields.append("academic_year")
                        if update_fields:
                            update_fields.append("updated_at")
                            existing.save(update_fields=update_fields)

            result = list(
                StudentUnitEnrollment.objects.filter(user=user, is_active=True)
                .select_related("academic_unit", "academic_level", "semester", "academic_year")
                .order_by("academic_unit__code")
            )
            setattr(user, "_active_unit_enrollments_cache", result)
            return result
        except Exception as exc:
            logger.debug("Fallback to in-memory programme units during sync for user=%s: %s", getattr(user, "id", None), exc)
            fallback = cls._build_fallback_enrollments(user)
            setattr(user, "_active_unit_enrollments_cache", fallback)
            return fallback

    @classmethod
    def get_active_enrollments(
        cls,
        user,
        auto_sync_if_empty: bool = True,
    ) -> List[StudentUnitEnrollment]:
        """Return active unit enrollments for a student, auto-syncing from their programme if needed."""
        if not user or not getattr(user, "is_authenticated", False):
            return []

        cached = getattr(user, "_active_unit_enrollments_cache", None)
        if cached is not None:
            return cached

        try:
            enrollments = list(
                StudentUnitEnrollment.objects.filter(
                    user=user,
                    is_active=True,
                    academic_unit__is_active=True,
                )
                .select_related("academic_unit", "academic_level", "semester", "academic_year")
                .order_by("academic_unit__code")
            )
            if enrollments:
                setattr(user, "_active_unit_enrollments_cache", enrollments)
                return enrollments

            if auto_sync_if_empty:
                return cls.sync_student_units(user)
            setattr(user, "_active_unit_enrollments_cache", [])
            return []
        except Exception as exc:
            logger.debug("Using ProgrammeUnit fallback for user=%s: %s", getattr(user, "id", None), exc)
            try:
                fallback = cls._build_fallback_enrollments(user)
            except Exception:
                fallback = []
            setattr(user, "_active_unit_enrollments_cache", fallback)
            return fallback

    @classmethod
    def enroll_unit(
        cls,
        user,
        academic_unit: AcademicUnit,
        source: str = "custom_elective",
    ) -> StudentUnitEnrollment:
        """Add or re-activate a specific AcademicUnit for a student."""
        _, academic_level, semester, academic_year = cls.resolve_student_programme_and_level(user)
        if source not in dict(StudentUnitEnrollment.ENROLLMENT_SOURCE_CHOICES):
            source = "custom_elective"

        enrollment, created = StudentUnitEnrollment.objects.update_or_create(
            user=user,
            academic_unit=academic_unit,
            defaults={
                "academic_level": academic_level,
                "semester": semester,
                "academic_year": academic_year,
                "source": source,
                "is_active": True,
            },
        )
        if hasattr(user, "_active_unit_enrollments_cache"):
            delattr(user, "_active_unit_enrollments_cache")
        return enrollment

    @classmethod
    def unenroll_unit(cls, user, academic_unit_id: int) -> bool:
        """Deactivate a unit enrollment for a student (persisting opt-out for auto_programme units)."""
        updated = StudentUnitEnrollment.objects.filter(
            user=user,
            academic_unit_id=academic_unit_id,
            is_active=True,
        ).update(is_active=False)
        if hasattr(user, "_active_unit_enrollments_cache"):
            delattr(user, "_active_unit_enrollments_cache")
        return updated > 0

    @classmethod
    def add_unit_to_programme(
        cls,
        user,
        programme: Programme,
        raw_code: str,
        raw_name: str,
        academic_level_ident,
        semester_ident,
        is_elective: bool = False,
    ) -> Tuple[AcademicUnit, ProgrammeUnit, bool, bool]:
        """Add or link a canonical AcademicUnit to a Programme's curriculum for a specific Year & Semester.

        If the canonical unit code already exists in `AcademicUnit` (e.g., shared across
        Mathematics Education and Computer Science), reuses that exact `AcademicUnit`
        so documents uploaded under that unit are shared seamlessly across programmes.
        """
        if not programme:
            raise ValueError("A valid programme is required to add a unit.")

        is_valid, canonical_code, error_msg = cls.validate_canonical_unit_code(raw_code)
        if not is_valid:
            raise ValueError(error_msg)

        clean_name = " ".join(str(raw_name or "").strip().split())

        level_obj = cls.resolve_or_ensure_academic_level(academic_level_ident)
        if not level_obj:
            raise ValueError("Please select a valid year of study (e.g., Year 1 - Year 4).")

        sem_obj = cls.resolve_or_ensure_semester(semester_ident)
        if not sem_obj:
            raise ValueError("Please select a valid semester (Semester 1 or Semester 2).")

        compact_code = canonical_code.replace(" ", "")
        with transaction.atomic():
            unit = AcademicUnit.objects.filter(
                Q(code__iexact=canonical_code) | Q(code__iexact=compact_code)
            ).first()
            unit_created = False

            if unit:
                update_fields = []
                if not unit.is_active:
                    unit.is_active = True
                    update_fields.append("is_active")
                if unit.code != canonical_code and not AcademicUnit.objects.filter(code__iexact=canonical_code).exclude(pk=unit.pk).exists():
                    unit.code = canonical_code
                    update_fields.append("code")
                if update_fields:
                    unit.save(update_fields=update_fields)
            else:
                if len(clean_name) < 2:
                    raise ValueError("Please provide the official unit name as shown in the student portal.")
                unit = AcademicUnit.objects.create(
                    code=canonical_code,
                    name=clean_name,
                    is_active=True,
                )
                unit_created = True

            # Check if ProgrammeUnit already exists for this programme, unit, level, and semester number
            existing_pu = (
                ProgrammeUnit.objects.filter(
                    programme=programme,
                    academic_unit=unit,
                    academic_level=level_obj,
                )
                .filter(Q(semester=sem_obj) | Q(semester__number=sem_obj.number))
                .first()
            )

            mapping_created = False
            if existing_pu:
                pu = existing_pu
                if pu.is_elective != bool(is_elective) or pu.is_core != (not bool(is_elective)):
                    pu.is_elective = bool(is_elective)
                    pu.is_core = not bool(is_elective)
                    pu.save(update_fields=["is_elective", "is_core"])
            else:
                pu = ProgrammeUnit.objects.create(
                    programme=programme,
                    academic_unit=unit,
                    academic_level=level_obj,
                    academic_year=sem_obj.academic_year,
                    semester=sem_obj,
                    is_core=not bool(is_elective),
                    is_elective=bool(is_elective),
                )
                mapping_created = True

            # Bridge to legacy CourseAcademicUnit if a matching legacy Course exists
            try:
                from courses.models import Course, CourseAcademicUnit, Year as LegacyYear

                legacy_course = Course.objects.filter(
                    Q(code__iexact=programme.code) | Q(name__iexact=programme.name)
                ).first()
                if legacy_course and level_obj:
                    legacy_year, _ = LegacyYear.objects.get_or_create(
                        course=legacy_course,
                        level=level_obj.level,
                    )
                    CourseAcademicUnit.objects.get_or_create(
                        course=legacy_course,
                        academic_unit=unit,
                        year_level=legacy_year,
                        semester=sem_obj,
                        defaults={
                            "is_core": not bool(is_elective),
                            "is_elective": bool(is_elective),
                        },
                    )
            except Exception as exc:
                logger.debug("Legacy CourseAcademicUnit bridge skipped: %s", exc)

        # Synchronize students currently in this programme and level
        try:
            from users.models import User

            affected_students = list(
                User.objects.filter(programme=programme, academic_level=level_obj, is_active=True)[:200]
            )
            if user and getattr(user, "is_authenticated", False) and all(s.id != user.id for s in affected_students):
                affected_students.append(user)

            from recommendations.services.document_recommender import invalidate_document_recommendations_cache

            for student in affected_students:
                if hasattr(student, "_active_unit_enrollments_cache"):
                    delattr(student, "_active_unit_enrollments_cache")
                cls.sync_student_units(student)
                invalidate_document_recommendations_cache(student.id)
        except Exception as exc:
            logger.debug("Post-add student sync warning: %s", exc)

        return unit, pu, unit_created, mapping_created

    @classmethod
    def update_student_academic_profile(
        cls,
        user,
        programme_id=None,
        academic_level_ident=None,
        semester_ident=None,
    ):
        """Update a student's enrolled programme, current year of study, and current semester."""
        if not user or not getattr(user, "is_authenticated", False):
            raise ValueError("Authentication required.")

        update_fields = []
        if programme_id:
            prog = Programme.objects.filter(pk=int(programme_id), is_active=True).first()
            if prog and user.programme_id != prog.id:
                user.programme = prog
                update_fields.append("programme")

        if academic_level_ident:
            lvl = cls.resolve_or_ensure_academic_level(academic_level_ident)
            if lvl and user.academic_level_id != lvl.id:
                user.academic_level = lvl
                update_fields.append("academic_level")

        if semester_ident:
            sem = cls.resolve_or_ensure_semester(semester_ident, getattr(user, "academic_year", None))
            if sem:
                if user.semester_id != sem.id:
                    user.semester = sem
                    update_fields.append("semester")
                if sem.academic_year_id and user.academic_year_id != sem.academic_year_id:
                    user.academic_year_id = sem.academic_year_id
                    update_fields.append("academic_year")

        # Keep legacy course & year bridged
        resolved_prog = user.programme
        resolved_lvl = user.academic_level
        if resolved_prog:
            try:
                from courses.models import Course, Year as LegacyYear

                matching_course = Course.objects.filter(
                    Q(code__iexact=resolved_prog.code) | Q(name__iexact=resolved_prog.name)
                ).first()
                if matching_course and user.course_id != matching_course.id:
                    user.course = matching_course
                    update_fields.append("course")
                if matching_course and resolved_lvl:
                    matching_year, _ = LegacyYear.objects.get_or_create(
                        course=matching_course,
                        level=resolved_lvl.level,
                    )
                    if user.year_id != matching_year.id:
                        user.year = matching_year
                        update_fields.append("year")
            except Exception:
                pass

        if update_fields:
            user.save()
        if hasattr(user, "_active_unit_enrollments_cache"):
            delattr(user, "_active_unit_enrollments_cache")
        cls.sync_student_units(user, force_resync=True)

        try:
            from recommendations.services.engine import UnifiedRecommendationEngine

            UnifiedRecommendationEngine.invalidate_all_user_caches(user.id)
        except Exception:
            pass

        return cls.get_student_academic_summary(user)

    @classmethod
    def get_units_for_context(
        cls,
        programme_id=None,
        level_ident=None,
        semester_ident=None,
        user=None,
        academic_level_ident=None,
    ) -> List[dict]:
        """Return serialized AcademicUnits for a given (Programme, Year, Semester) context.

        Used by both the Student Academic Dashboard and the Document Upload dynamic dropdown.
        """
        if level_ident is None and academic_level_ident is not None:
            level_ident = academic_level_ident

        programme = None
        if programme_id and str(programme_id).isdigit():
            programme = Programme.objects.filter(pk=int(programme_id)).first()
        elif user and getattr(user, "is_authenticated", False):
            programme, _, _, _ = cls.resolve_student_programme_and_level(user)

        level_obj = cls.resolve_or_ensure_academic_level(level_ident) if level_ident else None
        sem_obj = cls.resolve_or_ensure_semester(semester_ident) if semester_ident else None

        units_map = {}
        ordered_unit_ids = []

        if programme:
            pu_qs = ProgrammeUnit.objects.filter(
                programme=programme,
                academic_unit__is_active=True,
            ).select_related("academic_unit", "academic_level", "semester")

            if level_obj:
                pu_qs = pu_qs.filter(
                    Q(academic_level=level_obj) | Q(academic_level__level=level_obj.level)
                )
            if sem_obj:
                sem_q = Q(semester=sem_obj)
                if getattr(sem_obj, "number", None):
                    sem_q |= Q(semester__number=sem_obj.number)
                pu_qs = pu_qs.filter(sem_q)

            for pu in pu_qs.order_by("academic_unit__code"):
                u = pu.academic_unit
                if u.id not in units_map:
                    units_map[u.id] = {
                        "id": u.id,
                        "code": u.code,
                        "name": u.name,
                        "credit_hours": u.credit_hours,
                        "is_core": pu.is_core,
                        "is_elective": pu.is_elective,
                        "level_id": pu.academic_level_id,
                        "level_name": pu.academic_level.name if pu.academic_level else "",
                        "semester_number": pu.semester.number if pu.semester else None,
                    }
                    ordered_unit_ids.append(u.id)

        # If this context matches the authenticated user's active programme/level/semester,
        # also include any custom/retake enrolled units they have active.
        if user and getattr(user, "is_authenticated", False):
            u_prog, u_lvl, u_sem, _ = cls.resolve_student_programme_and_level(user)
            same_prog = programme and u_prog and programme.id == u_prog.id
            same_lvl = (not level_obj) or (u_lvl and level_obj.level == u_lvl.level)
            same_sem = (not sem_obj) or (u_sem and sem_obj.number == u_sem.number)
            if same_prog and same_lvl and same_sem:
                for enr in cls.get_active_enrollments(user, auto_sync_if_empty=False):
                    u = enr.academic_unit
                    if u and u.id not in units_map:
                        units_map[u.id] = {
                            "id": u.id,
                            "code": u.code,
                            "name": u.name,
                            "credit_hours": u.credit_hours,
                            "is_core": enr.source == "auto_programme",
                            "is_elective": enr.source != "auto_programme",
                            "level_id": enr.academic_level_id,
                            "level_name": enr.academic_level.name if enr.academic_level else "",
                            "semester_number": enr.semester.number if enr.semester else None,
                        }
                        ordered_unit_ids.append(u.id)

        if not ordered_unit_ids:
            return []

        # Annotate document counts and shared programmes
        from documents.documents.models import DocumentAcademicUnit

        doc_counts = dict(
            DocumentAcademicUnit.objects.filter(
                academic_unit_id__in=ordered_unit_ids,
                document__status="ready",
                document__is_available=True,
                document__visibility="public",
            )
            .values("academic_unit_id")
            .annotate(cnt=Count("document_id", distinct=True))
            .values_list("academic_unit_id", "cnt")
        )

        shared_map = {}
        shared_qs = (
            ProgrammeUnit.objects.filter(academic_unit_id__in=ordered_unit_ids)
            .select_related("programme")
            .values_list("academic_unit_id", "programme__code", "programme__name", "programme_id")
        )
        for u_id, p_code, p_name, p_id in shared_qs:
            if programme and p_id == programme.id:
                continue
            shared_map.setdefault(u_id, {})[p_code] = p_name

        result = []
        for u_id in ordered_unit_ids:
            item = units_map[u_id]
            item["document_count"] = doc_counts.get(u_id, 0)
            other_progs = shared_map.get(u_id, {})
            item["shared_programme_codes"] = sorted(other_progs.keys())
            item["shared_programmes_count"] = len(other_progs)
            result.append(item)

        return result

    @classmethod
    def get_student_academic_summary(cls, user) -> dict:
        """Return a comprehensive summary of the student's academic profile and active units."""
        programme, academic_level, semester, academic_year = cls.resolve_student_programme_and_level(user)
        enrollments = cls.get_active_enrollments(user) if (user and getattr(user, "is_authenticated", False)) else []

        department = getattr(programme, "department", None) if programme else None
        school = getattr(department, "school", None) if department else None
        faculty = getattr(school, "faculty", None) if school else None

        # Persist resolved programme/level/semester back to User if they were resolved via fallback
        if user and getattr(user, "is_authenticated", False):
            save_fields = []
            if programme and not getattr(user, "programme_id", None):
                user.programme = programme
                save_fields.append("programme")
            if academic_level and not getattr(user, "academic_level_id", None):
                user.academic_level = academic_level
                save_fields.append("academic_level")
            if semester and not getattr(user, "semester_id", None):
                user.semester = semester
                save_fields.append("semester")
            if academic_year and not getattr(user, "academic_year_id", None):
                user.academic_year = academic_year
                save_fields.append("academic_year")
            if save_fields:
                try:
                    user.save(update_fields=save_fields)
                except Exception:
                    pass

        serialized_enrolled_units = [
            {
                "id": enr.academic_unit_id,
                "code": enr.academic_unit.code,
                "name": enr.academic_unit.name,
                "source": enr.source,
                "credit_hours": enr.academic_unit.credit_hours,
            }
            for enr in enrollments
            if getattr(enr, "academic_unit", None)
        ]

        return {
            "programme": programme,
            "programme_id": programme.id if programme else None,
            "programme_code": programme.code if programme else "",
            "programme_name": programme.name if programme else "",
            "duration_years": (programme.duration_years or 4) if programme else 4,
            "department": department,
            "department_name": department.name if department else "",
            "department_code": department.code if department else "",
            "school": school,
            "school_name": school.name if school else "",
            "school_code": school.code if school else "",
            "faculty": faculty,
            "faculty_name": faculty.name if faculty else "",
            "academic_level": academic_level,
            "academic_level_id": academic_level.id if academic_level else None,
            "academic_level_number": academic_level.level if academic_level else 1,
            "academic_level_name": academic_level.name if academic_level else "Year 1",
            "semester": semester,
            "semester_id": semester.id if semester else None,
            "semester_number": semester.number if semester else 1,
            "semester_display": f"Semester {semester.number}" if semester else "Semester 1",
            "academic_year": academic_year,
            "academic_year_code": academic_year.code if academic_year else "",
            "enrolled_units": serialized_enrolled_units,
            "enrolled_count": len(serialized_enrolled_units),
            "enrolled_unit_ids": [item["id"] for item in serialized_enrolled_units],
        }



def document_academic_context(document, max_units=3) -> str:
    """Short academic provenance header for embedding/retrieval context.

    e.g. "Academic context: Unit MAT 101 Linear Algebra; Level Year 2; Semester 1."
    Returns '' when the document has no academic links.
    """
    try:
        links = list(
            document.academic_units
            .select_related('academic_unit', 'academic_level', 'semester__academic_year')
            [:max_units]
        )
    except Exception:
        return ''
    if not links:
        return ''
    parts = []
    for link in links:
        unit = getattr(link, 'academic_unit', None)
        if unit is None:
            continue
        entry = f"{unit.code} {unit.name}".strip()
        level = getattr(link, 'academic_level', None)
        if level is not None:
            entry += f" ({level.name})"
        parts.append(entry)
    if not parts:
        return ''
    return "Academic context: " + "; ".join(parts) + "."


class DocumentAcademicLinkService:
    """Resiliently links Documents to AcademicUnits and syncs DocumentSearchIndex."""

    @classmethod
    def link_document_to_unit(
        cls,
        document,
        academic_unit_id,
        academic_level_id=None,
        semester_id=None,
        academic_year_id=None,
        uploader=None,
        replace_existing: bool = False,
    ):
        """Link `document` to `academic_unit_id` even when semester/year/level are omitted in forms."""
        from documents.documents.models import DocumentAcademicUnit

        if not document or not academic_unit_id:
            if replace_existing and document:
                DocumentAcademicUnit.objects.filter(document=document).delete()
                cls._sync_search_index_units(document)
            return None

        unit = None
        if isinstance(academic_unit_id, AcademicUnit):
            unit = academic_unit_id
        elif str(academic_unit_id).isdigit():
            unit = AcademicUnit.objects.filter(pk=int(academic_unit_id)).first()
        else:
            unit = AcademicUnit.objects.filter(code__iexact=str(academic_unit_id).strip()).first()

        if not unit:
            return None

        # Infer missing academic_level, semester, and academic_year from:
        # 1. Explicit IDs
        # 2. ProgrammeUnit mapping for this unit (preferring uploader's programme if available)
        # 3. Uploader's profile / current active Semester & AcademicYear
        pu_qs = ProgrammeUnit.objects.filter(academic_unit=unit).select_related(
            "academic_level", "semester", "academic_year"
        )
        if uploader and getattr(uploader, "programme_id", None):
            preferred_pu = pu_qs.filter(programme_id=uploader.programme_id).first() or pu_qs.first()
        else:
            preferred_pu = pu_qs.first()

        level = None
        if academic_level_id and str(academic_level_id).isdigit():
            level = AcademicLevel.objects.filter(pk=int(academic_level_id)).first()
        if not level and preferred_pu and preferred_pu.academic_level_id:
            level = preferred_pu.academic_level
        if not level and uploader and getattr(uploader, "academic_level_id", None):
            level = uploader.academic_level

        semester = None
        if semester_id and str(semester_id).isdigit():
            semester = Semester.objects.filter(pk=int(semester_id)).first()
        if not semester and preferred_pu and preferred_pu.semester_id:
            semester = preferred_pu.semester
        if not semester and uploader and getattr(uploader, "semester_id", None):
            semester = uploader.semester
        if not semester:
            semester = Semester.objects.filter(is_current=True).first() or Semester.objects.first()

        academic_year = None
        if academic_year_id and str(academic_year_id).isdigit():
            academic_year = AcademicYear.objects.filter(pk=int(academic_year_id)).first()
        if not academic_year and semester and getattr(semester, "academic_year_id", None):
            academic_year = semester.academic_year
        if not academic_year and preferred_pu and preferred_pu.academic_year_id:
            academic_year = preferred_pu.academic_year
        if not academic_year and uploader and getattr(uploader, "academic_year_id", None):
            academic_year = uploader.academic_year
        if not academic_year:
            academic_year = AcademicYear.objects.filter(is_current=True).first() or AcademicYear.objects.first()

        with transaction.atomic():
            if replace_existing:
                DocumentAcademicUnit.objects.filter(document=document).exclude(academic_unit=unit).delete()

            existing = DocumentAcademicUnit.objects.filter(document=document, academic_unit=unit).first()
            if existing:
                existing.academic_level = level
                existing.semester = semester
                existing.academic_year = academic_year
                existing.is_primary = True
                existing.save()
                doc_unit = existing
            else:
                doc_unit = DocumentAcademicUnit.objects.create(
                    document=document,
                    academic_unit=unit,
                    academic_level=level,
                    semester=semester,
                    academic_year=academic_year,
                    is_primary=True,
                )

        cls._sync_search_index_units(document)
        return doc_unit

    @classmethod
    def infer_units_from_text(cls, text, limit=3):
        """Match academic units mentioned in free text by code or name.

        Codes match on word boundaries in both canonical ("MAT 101") and
        compact ("MAT101") forms; names must be >=6 chars and match on word
        boundaries to avoid substring noise ("Data" vs "Database Systems").
        """
        text = (text or '').strip()
        if len(text) < 3:
            return []
        haystack = text.upper()
        haystack_lower = text.lower()
        # Separator-normalized copy so "CSC-201" / "CSC_201" / "CSC.201" all
        # compare equal to the spaced canonical form "CSC 201".
        haystack_norm = re.sub(r'[\s\-_\.]+', ' ', haystack)
        matches = []
        for unit in AcademicUnit.objects.filter(is_active=True).only('id', 'code', 'name'):
            code = (unit.code or '').strip()
            compact = re.sub(r'[\s\-_\.]+', '', code).upper()
            spaced = re.sub(r'([A-Z]+)([A-Z0-9].*)', r'\1 \2', compact)
            code_hit = False
            for variant in {code.upper(), compact}:
                if variant and re.search(r'\b' + re.escape(variant) + r'\b', haystack):
                    code_hit = True
                    break
            if not code_hit and spaced and re.search(
                r'\b' + re.escape(spaced) + r'\b', haystack_norm
            ):
                code_hit = True
            name = (unit.name or '').strip()
            name_hit = len(name) >= 6 and re.search(
                r'\b' + re.escape(name.lower()) + r'\b', haystack_lower
            ) is not None
            if code_hit or name_hit:
                # Code matches are more precise than name matches.
                matches.append((0 if code_hit else 1, unit))
        matches.sort(key=lambda item: item[0])
        return [unit for _, unit in matches[:limit]]

    @classmethod
    def auto_link_document(cls, document, limit=3):
        """Tag a document with inferred academic units when it has none.

        Used by ingestion/backfill so untagged uploads still participate in
        unit-scoped retrieval and academic ranking. Returns linked units.
        """
        if not document or document.academic_units.exists():
            return []
        parts = [document.title or '', document.description or '']
        try:
            parts.extend(dt.tag.name for dt in document.document_tags.select_related('tag'))
        except Exception:
            pass
        units = cls.infer_units_from_text(' '.join(parts), limit=limit)
        linked = []
        for unit in units:
            try:
                cls.link_document_to_unit(
                    document=document,
                    academic_unit_id=unit.id,
                    uploader=getattr(document, 'uploaded_by', None),
                    replace_existing=False,
                )
                linked.append(unit)
            except Exception as exc:
                logger.warning("Auto-link failed for document %s unit %s: %s",
                               getattr(document, 'id', None), unit.code, exc)
        if linked:
            logger.info("Auto-linked document %s (%r) to units %s",
                        document.id, document.title, [u.code for u in linked])
        return linked

    @classmethod
    def _sync_search_index_units(cls, document) -> None:
        """Keep DocumentSearchIndex.academic_unit_codes synchronized after unit linkage changes."""
        try:
            from documents.search.models import DocumentSearchIndex

            unit_codes = list(
                document.academic_units.select_related("academic_unit")
                .values_list("academic_unit__code", flat=True)
            )
            DocumentSearchIndex.objects.filter(document=document).update(
                academic_unit_codes=unit_codes
            )
        except Exception as exc:
            logger.debug("Search index unit sync skipped for document %s: %s", getattr(document, "id", None), exc)
