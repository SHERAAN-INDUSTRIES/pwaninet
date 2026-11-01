"""
Timetable Extraction and Schedule Management Service for Vasto.
Processes timetable images, converts them into canonical JSON schedules,
computes free revision blocks, and maintains cohort timetable lifecycle.
"""

import base64
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from django.conf import settings
from django.core.files.base import ContentFile

from documents.academic.models import (
    AcademicLevel,
    AcademicYear,
    Institution,
    Programme,
    ProgrammeTimetable,
    Semester,
    TimetableStatus,
    TimetableType,
)

logger = logging.getLogger(__name__)

DAY_INDEX_MAP = {
    'monday': 1,
    'tuesday': 2,
    'wednesday': 3,
    'thursday': 4,
    'friday': 5,
    'saturday': 6,
    'sunday': 7,
}

DAYS_ORDER = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']


def compute_free_slots(weekly_schedule: List[Dict[str, Any]], start_hour: int = 8, end_hour: int = 17) -> List[Dict[str, Any]]:
    """
    Compute gaps/free study slots between start_hour (default 08:00) and end_hour (default 17:00)
    for each weekday (Monday to Friday).
    """
    free_slots = []
    
    # Group schedule by day
    schedule_by_day: Dict[str, List[Dict[str, Any]]] = {day: [] for day in DAYS_ORDER}
    for slot in weekly_schedule:
        day = slot.get('day', '').capitalize()
        if day in schedule_by_day:
            schedule_by_day[day].append(slot)

    for day in DAYS_ORDER:
        day_slots = schedule_by_day[day]
        day_index = DAY_INDEX_MAP.get(day.lower(), 1)
        
        # Sort slots by start_time
        parsed_slots = []
        for s in day_slots:
            try:
                st = datetime.strptime(s.get('start_time', '08:00'), '%H:%M').time()
                et = datetime.strptime(s.get('end_time', '09:00'), '%H:%M').time()
                parsed_slots.append((st, et))
            except Exception:
                continue
        parsed_slots.sort(key=lambda x: x[0])

        current_time = datetime.strptime(f"{start_hour:02d}:00", '%H:%M').time()
        end_boundary = datetime.strptime(f"{end_hour:02d}:00", '%H:%M').time()

        for st, et in parsed_slots:
            if st > current_time:
                # Found free interval
                diff_minutes = (datetime.combine(datetime.min, st) - datetime.combine(datetime.min, current_time)).total_seconds() / 60
                if diff_minutes >= 45:  # At least 45 minutes gap
                    duration_hrs = round(diff_minutes / 60.0, 1)
                    free_slots.append({
                        'day': day,
                        'day_index': day_index,
                        'start_time': current_time.strftime('%H:%M'),
                        'end_time': st.strftime('%H:%M'),
                        'duration_hours': duration_hrs,
                    })
            if et > current_time:
                current_time = et

        if current_time < end_boundary:
            diff_minutes = (datetime.combine(datetime.min, end_boundary) - datetime.combine(datetime.min, current_time)).total_seconds() / 60
            if diff_minutes >= 45:
                duration_hrs = round(diff_minutes / 60.0, 1)
                free_slots.append({
                    'day': day,
                    'day_index': day_index,
                    'start_time': current_time.strftime('%H:%M'),
                    'end_time': end_boundary.strftime('%H:%M'),
                    'duration_hours': duration_hrs,
                })

    return free_slots


class TimetableExtractionService:
    """
    Multimodal Vision and OCR extraction service for academic timetables.
    """

    @classmethod
    def extract_from_image(
        cls,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
        programme_name: str = "",
        level_name: str = "",
        institution_name: str = "",
    ) -> Dict[str, Any]:
        """
        Process the timetable image using Gemini Vision (or structured fallback)
        and construct the canonical schedule JSON.
        """
        api_key = getattr(settings, "PWANIMATE_GEMINI_API_KEY", "") or getattr(settings, "GEMINI_API_KEY", "")
        raw_model = getattr(settings, "PWANIMATE_GEMINI_MODEL", "gemini-3.8-flash")
        # Automatically normalize any deprecated model strings from old environment configs
        deprecated_models = {"gemini-2.5-flash", "gemini-1.5-flash", "gemini-2.5-pro", "gemini-1.5-pro"}
        model_name = "gemini-3.8-flash" if (not raw_model or raw_model in deprecated_models) else raw_model

        if api_key and api_key.strip():
            try:
                extracted = cls._call_gemini_vision(
                    image_bytes=image_bytes,
                    mime_type=mime_type,
                    api_key=api_key.strip(),
                    model_name=model_name,
                    programme_name=programme_name,
                    level_name=level_name,
                    institution_name=institution_name,
                )
                if extracted and isinstance(extracted, dict) and extracted.get('weekly_schedule'):
                    # Augment with computed free slots
                    weekly_schedule = extracted['weekly_schedule']
                    # Ensure day_index is populated
                    for slot in weekly_schedule:
                        d = str(slot.get('day', '')).capitalize()
                        slot['day'] = d
                        slot['day_index'] = DAY_INDEX_MAP.get(d.lower(), 1)
                        if not slot.get('id'):
                            raw_id = f"{d[:3].lower()}_{slot.get('start_time', '00').replace(':', '')}_{slot.get('unit_code', 'unit')}"
                            slot['id'] = re.sub(r'[^a-zA-Z0-9_]', '', raw_id)

                    extracted['free_slots'] = compute_free_slots(weekly_schedule)
                    return extracted
            except Exception as e:
                logger.warning(f"[TIMETABLE-EXTRACT] Gemini Vision extraction error: {e}. Falling back to default parser.")

        # Fallback starter schedule if Vision API is unreachable
        return cls._create_starter_schedule(programme_name, level_name, institution_name)

    @classmethod
    def _call_gemini_vision(
        cls,
        image_bytes: bytes,
        mime_type: str,
        api_key: str,
        model_name: str,
        programme_name: str,
        level_name: str,
        institution_name: str,
    ) -> Optional[Dict[str, Any]]:
        """Call Gemini REST generateContent with image and strict JSON output schema."""
        b64_image = base64.b64encode(image_bytes).decode('utf-8')
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"

        prompt = f"""
You are an expert academic timetable parser. Analyze this timetable image for {programme_name} ({level_name}) at {institution_name}.
Extract every lecture, lab, seminar, or class slot from the grid into a clean JSON structure.

Return ONLY a valid JSON object matching this schema:
{{
  "title": "Semester Timetable",
  "weekly_schedule": [
    {{
      "id": "mon_0800_unitcode",
      "day": "Monday",
      "day_index": 1,
      "start_time": "08:00",
      "end_time": "11:00",
      "unit_code": "CSC 221",
      "unit_name": "Database Systems",
      "venue": "LH1",
      "lecturer": "Dr. Name",
      "session_type": "Lecture"
    }}
  ]
}}

Rules:
1. "day" must be one of: Monday, Tuesday, Wednesday, Thursday, Friday, Saturday.
2. "start_time" and "end_time" must be in 24-hour HH:MM format (e.g. "08:00", "14:00").
3. "session_type" can be "Lecture", "Lab", "Tutorial", "Practical", or "Seminar".
4. If venue or lecturer is not visible in the box, use an empty string or null.
5. Output pure JSON without markdown wrappers or codeblocks.
"""

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": b64_image,
                            }
                        }
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "response_mime_type": "application/json",
            }
        }

        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            try:
                candidate = data["candidates"][0]["content"]["parts"][0]["text"]
                clean_json = candidate.strip()
                if clean_json.startswith("```"):
                    clean_json = re.sub(r"^```[a-zA-Z]*\n?", "", clean_json)
                    clean_json = re.sub(r"\n?```$", "", clean_json)
                return json.loads(clean_json)
            except (KeyError, IndexError, json.JSONDecodeError) as e:
                logger.error(f"[TIMETABLE-EXTRACT] Failed to parse JSON response from Gemini: {e}")
        elif resp.status_code == 404 and model_name != "gemini-3.8-flash":
            logger.warning(
                f"[TIMETABLE-EXTRACT] Gemini model '{model_name}' returned 404; automatically retrying with 'gemini-3.8-flash'..."
            )
            return cls._call_gemini_vision(
                image_bytes=image_bytes,
                mime_type=mime_type,
                api_key=api_key,
                model_name="gemini-3.8-flash",
                programme_name=programme_name,
                level_name=level_name,
                institution_name=institution_name,
            )
        else:
            logger.warning(f"[TIMETABLE-EXTRACT] Gemini API returned status={resp.status_code}: {resp.text[:200]}")

        return None

    @classmethod
    def _create_starter_schedule(
        cls,
        programme_name: str,
        level_name: str,
        institution_name: str
    ) -> Dict[str, Any]:
        """Fallback starter schedule when external AI gateway is offline."""
        schedule = [
            {
                "id": "mon_0800_session1",
                "day": "Monday",
                "day_index": 1,
                "start_time": "08:00",
                "end_time": "11:00",
                "unit_code": "LEC 101",
                "unit_name": f"{programme_name or 'Core'} Lecture 1",
                "venue": "Lecture Hall 1",
                "lecturer": "",
                "session_type": "Lecture"
            },
            {
                "id": "wed_1000_session2",
                "day": "Wednesday",
                "day_index": 3,
                "start_time": "10:00",
                "end_time": "13:00",
                "unit_code": "LEC 102",
                "unit_name": f"{programme_name or 'Core'} Lecture 2",
                "venue": "Lecture Hall 2",
                "lecturer": "",
                "session_type": "Lecture"
            },
            {
                "id": "fri_1400_session3",
                "day": "Friday",
                "day_index": 5,
                "start_time": "14:00",
                "end_time": "17:00",
                "unit_code": "PRA 103",
                "unit_name": "Practical / Lab Session",
                "venue": "Lab Complex",
                "lecturer": "",
                "session_type": "Practical"
            }
        ]
        return {
            "title": f"{programme_name} ({level_name}) Schedule",
            "weekly_schedule": schedule,
            "free_slots": compute_free_slots(schedule)
        }


class ProgrammeTimetableManager:
    """Service facade for managing cohort timetables and Spark context delivery."""

    @staticmethod
    def get_active_timetable(
        institution: Optional[Institution],
        programme: Optional[Programme],
        academic_level: Optional[AcademicLevel],
        semester: Optional[Semester] = None,
        timetable_type: str = TimetableType.LECTURE,
        include_processing: bool = True,
    ) -> Optional[ProgrammeTimetable]:
        """Fetch active (or currently processing) timetable for a specific student cohort."""
        from django.db.models import Q
        if not programme or not academic_level:
            return None

        # Include PROCESSING so the dashboard panel shows the Spark AI shimmer animation and HTMX auto-poll
        status_q = Q(is_active=True)
        if include_processing:
            status_q |= Q(status=TimetableStatus.PROCESSING)

        qs = ProgrammeTimetable.objects.filter(
            status_q,
            programme=programme,
            academic_level=academic_level,
            timetable_type=timetable_type,
        )

        if institution:
            qs_inst = qs.filter(Q(institution=institution) | Q(institution__isnull=True))
            if qs_inst.exists():
                qs = qs_inst

        if semester:
            # Try matching specific semester first; fall back to unmapped or cohort-wide timetable
            qs_sem = qs.filter(semester=semester)
            if qs_sem.exists():
                return qs_sem.order_by('-revision_number', '-created_at').first()

        return qs.order_by('-revision_number', '-created_at').first()

    @staticmethod
    def save_timetable_image(
        user,
        image_file,
        institution: Institution,
        programme: Programme,
        academic_level: AcademicLevel,
        semester: Optional[Semester] = None,
        academic_year: Optional[AcademicYear] = None,
        timetable_type: str = TimetableType.LECTURE,
        change_summary: str = "",
        custom_schedule: Optional[Dict[str, Any]] = None,
    ) -> ProgrammeTimetable:
        """
        Phase 1 (sync) — Persist the uploaded image and create a ProgrammeTimetable
        record with status=PROCESSING.  Returns immediately; the AI extraction is
        handled asynchronously by ``process_timetable_image_task``.

        If *custom_schedule* is provided (e.g. tests or manual entry) the schedule
        is stored straight away and the record is activated without a Celery task.
        """
        from documents.tasks.timetable import process_timetable_image_task

        # ── Revision number ───────────────────────────────────────────────────
        existing_rev = ProgrammeTimetable.objects.filter(
            institution=institution,
            programme=programme,
            academic_level=academic_level,
            timetable_type=timetable_type,
        ).order_by('-revision_number').first()

        new_rev_number = (existing_rev.revision_number + 1) if existing_rev else 1

        # ── Supersede previous active timetable for this cohort ───────────────
        ProgrammeTimetable.objects.filter(
            institution=institution,
            programme=programme,
            academic_level=academic_level,
            timetable_type=timetable_type,
            is_active=True,
        ).update(is_active=False, status=TimetableStatus.SUPERSEDED)

        title = f"{programme.code} {academic_level.name} Timetable (Rev {new_rev_number})"

        # ── Shortcut: custom_schedule provided (no AI needed) ─────────────────
        if custom_schedule:
            schedule_data = custom_schedule
            if not schedule_data.get('free_slots'):
                schedule_data['free_slots'] = compute_free_slots(
                    schedule_data.get('weekly_schedule', [])
                )
            timetable = ProgrammeTimetable.objects.create(
                institution=institution,
                programme=programme,
                academic_level=academic_level,
                academic_year=academic_year or getattr(user, 'academic_year', None),
                semester=semester or getattr(user, 'semester', None),
                timetable_type=timetable_type,
                status=TimetableStatus.ACTIVE,
                revision_number=new_rev_number,
                title=title,
                change_summary=change_summary,
                source_image=image_file,
                schedule_data=schedule_data,
                is_active=True,
                uploaded_by=user,
            )
            logger.info(
                '[TIMETABLE] Custom-schedule timetable created (id=%s, rev=%s).',
                timetable.id, new_rev_number,
            )
            return timetable

        # ── Normal path: create PROCESSING record, fire Celery task ──────────
        timetable = ProgrammeTimetable.objects.create(
            institution=institution,
            programme=programme,
            academic_level=academic_level,
            academic_year=academic_year or getattr(user, 'academic_year', None),
            semester=semester or getattr(user, 'semester', None),
            timetable_type=timetable_type,
            status=TimetableStatus.PROCESSING,
            revision_number=new_rev_number,
            title=title,
            change_summary=change_summary,
            source_image=image_file,
            schedule_data={},           # populated by Celery task
            is_active=False,            # goes True once task completes
            uploaded_by=user,
        )

        logger.info(
            '[TIMETABLE] Record created (id=%s, rev=%s) — dispatching Celery task.',
            timetable.id, new_rev_number,
        )

        # Dispatch — countdowns allow the DB transaction to commit first
        process_timetable_image_task.apply_async(
            args=[timetable.id],
            countdown=2,
            queue='docs_queue',
        )

        return timetable

    @staticmethod
    def save_timetable_image_from_storage(
        user,
        storage_path: str,
        institution: Institution,
        programme: Programme,
        academic_level: AcademicLevel,
        semester: Optional[Semester] = None,
        academic_year: Optional[AcademicYear] = None,
        timetable_type: str = TimetableType.LECTURE,
        change_summary: str = "",
    ) -> ProgrammeTimetable:
        """Same as ``save_timetable_image`` but for an image that is already
        in object storage (uploaded straight to the bucket by the browser).

        The record is created in PROCESSING state and the AI extraction runs
        in the background, exactly like the classic upload path.
        """
        from documents.tasks.timetable import process_timetable_image_task

        existing_rev = ProgrammeTimetable.objects.filter(
            institution=institution,
            programme=programme,
            academic_level=academic_level,
            timetable_type=timetable_type,
        ).order_by('-revision_number').first()

        new_rev_number = (existing_rev.revision_number + 1) if existing_rev else 1

        ProgrammeTimetable.objects.filter(
            institution=institution,
            programme=programme,
            academic_level=academic_level,
            timetable_type=timetable_type,
            is_active=True,
        ).update(is_active=False, status=TimetableStatus.SUPERSEDED)

        title = f"{programme.code} {academic_level.name} Timetable (Rev {new_rev_number})"

        timetable = ProgrammeTimetable.objects.create(
            institution=institution,
            programme=programme,
            academic_level=academic_level,
            academic_year=academic_year or getattr(user, 'academic_year', None),
            semester=semester or getattr(user, 'semester', None),
            timetable_type=timetable_type,
            status=TimetableStatus.PROCESSING,
            revision_number=new_rev_number,
            title=title,
            change_summary=change_summary,
            schedule_data={},           # populated by Celery task
            is_active=False,            # goes True once task completes
            uploaded_by=user,
        )
        # Point the image field at the object that is already in the bucket.
        timetable.source_image.name = storage_path
        timetable.save(update_fields=['source_image'])

        logger.info(
            '[TIMETABLE] Record created (id=%s, rev=%s) from direct upload — dispatching Celery task.',
            timetable.id, new_rev_number,
        )

        process_timetable_image_task.apply_async(
            args=[timetable.id],
            countdown=2,
            queue='docs_queue',
        )

        return timetable

    @staticmethod
    def mutate_timetable_slot(
        timetable: ProgrammeTimetable,
        action: str,  # 'add', 'update', 'delete'
        slot_data: Optional[Dict[str, Any]] = None,
        slot_id: Optional[str] = None,
        user=None,
    ) -> ProgrammeTimetable:
        """
        Safely update, add, or delete an individual slot in the timetable schedule
        and automatically recompute free study slots.
        """
        schedule_data = timetable.schedule_data or {}
        weekly_schedule = list(schedule_data.get('weekly_schedule', []))

        if action == 'delete' and slot_id:
            weekly_schedule = [s for s in weekly_schedule if str(s.get('id')) != str(slot_id)]

        elif action == 'add' and slot_data:
            day = str(slot_data.get('day', 'Monday')).capitalize()
            start_t = str(slot_data.get('start_time', '08:00')).strip()
            unit_c = str(slot_data.get('unit_code', 'UNIT')).strip()
            gen_id = slot_data.get('id') or f"{day[:3].lower()}_{start_t.replace(':', '')}_{unit_c.replace(' ', '').lower()}"

            new_slot = {
                'id': gen_id,
                'day': day,
                'day_index': DAY_INDEX_MAP.get(day.lower(), 1),
                'start_time': start_t,
                'end_time': str(slot_data.get('end_time', '11:00')).strip(),
                'unit_code': unit_c,
                'unit_name': str(slot_data.get('unit_name', '')).strip(),
                'venue': str(slot_data.get('venue', '')).strip(),
                'lecturer': str(slot_data.get('lecturer', '')).strip(),
                'session_type': str(slot_data.get('session_type', 'Lecture')).strip(),
            }
            weekly_schedule.append(new_slot)

        elif action == 'update' and slot_data:
            target_id = str(slot_data.get('id') or slot_id or '')
            day = str(slot_data.get('day', '')).capitalize()
            for idx, s in enumerate(weekly_schedule):
                if str(s.get('id')) == target_id:
                    weekly_schedule[idx] = {
                        'id': target_id,
                        'day': day or s.get('day', 'Monday'),
                        'day_index': DAY_INDEX_MAP.get((day or s.get('day', 'Monday')).lower(), 1),
                        'start_time': str(slot_data.get('start_time', s.get('start_time', '08:00'))).strip(),
                        'end_time': str(slot_data.get('end_time', s.get('end_time', '11:00'))).strip(),
                        'unit_code': str(slot_data.get('unit_code', s.get('unit_code', ''))).strip(),
                        'unit_name': str(slot_data.get('unit_name', s.get('unit_name', ''))).strip(),
                        'venue': str(slot_data.get('venue', s.get('venue', ''))).strip(),
                        'lecturer': str(slot_data.get('lecturer', s.get('lecturer', ''))).strip(),
                        'session_type': str(slot_data.get('session_type', s.get('session_type', 'Lecture'))).strip(),
                    }
                    break

        # Re-sort weekly schedule by day_index then start_time
        weekly_schedule.sort(key=lambda s: (s.get('day_index', 1), s.get('start_time', '00:00')))

        # Recompute free slots
        schedule_data['weekly_schedule'] = weekly_schedule
        schedule_data['free_slots'] = compute_free_slots(weekly_schedule)

        timetable.schedule_data = schedule_data
        if user:
            timetable.verified_by = user
        timetable.save(update_fields=['schedule_data', 'verified_by', 'updated_at'])
        return timetable

