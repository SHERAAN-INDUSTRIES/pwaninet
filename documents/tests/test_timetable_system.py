"""
Unit & Integration Tests for Cohort Timetable System,
Cropper Pipeline, Free Slot Computation, and Spark AI Context.

Design principle:
  Tests that need an ACTIVE timetable pass ``custom_schedule`` to
  ``save_timetable_image()`` — this bypasses Celery/Gemini and instantly
  creates an ACTIVE record (the shortcut path).

  One dedicated test (``test_processing_state_without_custom_schedule``)
  verifies the async path by mocking the Celery dispatch.
"""

from io import BytesIO
from unittest.mock import patch

from PIL import Image
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client
from django.urls import reverse

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
from documents.academic.timetable_service import (
    compute_free_slots,
    ProgrammeTimetableManager,
    TimetableExtractionService,
)
from pwanimate.context.types import StudentContext
from pwanimate.tools.domain.academic import StudentTimetableTool

User = get_user_model()

# ---------------------------------------------------------------------------
# Fixture schedule — pre-built, avoids any Gemini / network calls in tests.
# ---------------------------------------------------------------------------
SAMPLE_SCHEDULE = {
    "title": "Test Timetable",
    "weekly_schedule": [
        {
            "id": "mon_0800_csc221",
            "day": "Monday",
            "day_index": 1,
            "start_time": "08:00",
            "end_time": "11:00",
            "unit_code": "CSC 221",
            "unit_name": "Database Systems",
            "venue": "LH1",
            "lecturer": "Dr. Test",
            "session_type": "Lecture",
        },
        {
            "id": "wed_1400_csc222",
            "day": "Wednesday",
            "day_index": 3,
            "start_time": "14:00",
            "end_time": "17:00",
            "unit_code": "CSC 222",
            "unit_name": "Operating Systems",
            "venue": "LH2",
            "lecturer": "",
            "session_type": "Lecture",
        },
    ],
}


class TimetableSystemTests(TestCase):

    # ── setUp ─────────────────────────────────────────────────────────────────

    def setUp(self):
        from datetime import date
        from documents.academic.models import School, Department

        self.institution, _ = Institution.objects.get_or_create(
            slug="pwani-university",
            defaults={"name": "Pwani University", "short_name": "PU", "is_active": True},
        )

        self.programme = Programme.objects.filter(is_active=True).first()
        if not self.programme:
            school, _ = School.objects.get_or_create(
                code="SPAS",
                defaults={"name": "School of Pure and Applied Sciences", "institution": self.institution},
            )
            dept, _ = Department.objects.get_or_create(
                code="MATHS",
                defaults={"name": "Department of Mathematics and Computer Science", "school": school},
            )
            self.programme, _ = Programme.objects.get_or_create(
                code="BSC-CS",
                defaults={
                    "department": dept,
                    "name": "Bachelor of Science in Computer Science",
                    "degree_type": "Bachelor",
                    "duration_years": 4,
                    "is_active": True,
                },
            )

        self.academic_level, _ = AcademicLevel.objects.get_or_create(
            level=2,
            defaults={"name": "Year 2", "is_active": True},
        )

        self.academic_year = AcademicYear.objects.filter(is_current=True).first()
        if not self.academic_year:
            self.academic_year, _ = AcademicYear.objects.get_or_create(
                code="2026/2027",
                defaults={
                    "name": "Academic Year 2026/2027",
                    "start_date": date(2026, 9, 1),
                    "end_date": date(2027, 8, 31),
                    "is_current": True,
                },
            )

        self.semester = Semester.objects.filter(academic_year=self.academic_year, number=1).first()
        if not self.semester:
            self.semester, _ = Semester.objects.get_or_create(
                academic_year=self.academic_year,
                number=1,
                defaults={
                    "start_date": date(2026, 9, 1),
                    "end_date": date(2027, 1, 31),
                    "is_current": True,
                },
            )

        User.objects.filter(username="student_john").delete()
        self.user = User.objects.create_user(
            username="student_john",
            email="john@pu.ac.ke",
            password="secretpassword123",
            first_name="John",
            last_name="Doe",
            institution=self.institution,
            programme=self.programme,
            academic_level=self.academic_level,
            semester=self.semester,
        )
        self.client = Client()
        self.client.login(username="student_john", password="secretpassword123")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _create_sample_image(self):
        """Generate a minimal valid 100×100 JPEG in-memory."""
        img = Image.new("RGB", (100, 100), color=(255, 255, 255))
        buf = BytesIO()
        img.save(buf, format="JPEG")
        buf.seek(0)
        return SimpleUploadedFile("sample_timetable.jpg", buf.read(), content_type="image/jpeg")

    def _save_active_timetable(self, change_summary=""):
        """
        Create an ACTIVE timetable instantly via custom_schedule shortcut.
        No Celery worker or Gemini API key required.
        """
        return ProgrammeTimetableManager.save_timetable_image(
            user=self.user,
            image_file=self._create_sample_image(),
            institution=self.institution,
            programme=self.programme,
            academic_level=self.academic_level,
            semester=self.semester,
            custom_schedule=dict(SAMPLE_SCHEDULE),
            change_summary=change_summary,
        )

    # ── Tests ─────────────────────────────────────────────────────────────────

    def test_compute_free_slots(self):
        """Free study blocks are calculated correctly between lectures."""
        weekly_schedule = [
            {"day": "Monday", "start_time": "08:00", "end_time": "10:00",
             "unit_code": "CSC 221", "unit_name": "Database Systems"},
            {"day": "Monday", "start_time": "14:00", "end_time": "17:00",
             "unit_code": "CSC 222", "unit_name": "Operating Systems"},
        ]
        free_slots = compute_free_slots(weekly_schedule, start_hour=8, end_hour=17)
        mon_free = [f for f in free_slots if f["day"] == "Monday"]
        self.assertEqual(len(mon_free), 1)
        self.assertEqual(mon_free[0]["start_time"], "10:00")
        self.assertEqual(mon_free[0]["end_time"], "14:00")
        self.assertEqual(mon_free[0]["duration_hours"], 4.0)

    def test_save_timetable_image_and_supersede(self):
        """Rev 1 is created ACTIVE; uploading rev 2 supersedes rev 1."""
        tt1 = self._save_active_timetable()
        self.assertEqual(tt1.revision_number, 1)
        self.assertTrue(tt1.is_active)
        self.assertEqual(tt1.status, TimetableStatus.ACTIVE)

        tt2 = self._save_active_timetable(change_summary="Rescheduled Wednesday practical")
        self.assertEqual(tt2.revision_number, 2)
        self.assertTrue(tt2.is_active)
        self.assertEqual(tt2.status, TimetableStatus.ACTIVE)

        tt1.refresh_from_db()
        self.assertFalse(tt1.is_active)
        self.assertEqual(tt1.status, TimetableStatus.SUPERSEDED)

        active = ProgrammeTimetableManager.get_active_timetable(
            institution=self.institution,
            programme=self.programme,
            academic_level=self.academic_level,
            semester=self.semester,
        )
        self.assertEqual(active.id, tt2.id)

    def test_processing_state_without_custom_schedule(self):
        """
        Without custom_schedule the record starts PROCESSING and the Celery
        task is dispatched.  The dispatch is mocked — no worker needed.
        """
        with patch(
            'documents.tasks.timetable.process_timetable_image_task.apply_async'
        ) as mock_dispatch, self.captureOnCommitCallbacks(execute=True):
            tt = ProgrammeTimetableManager.save_timetable_image(
                user=self.user,
                image_file=self._create_sample_image(),
                institution=self.institution,
                programme=self.programme,
                academic_level=self.academic_level,
                semester=self.semester,
            )

        self.assertEqual(tt.status, TimetableStatus.PROCESSING)
        self.assertFalse(tt.is_active)
        self.assertEqual(tt.schedule_data, {})
        mock_dispatch.assert_called_once()
        dispatched_id = mock_dispatch.call_args[1]['args'][0]
        self.assertEqual(dispatched_id, tt.id)

    def test_mutate_timetable_slot(self):
        """Add a slot to the timetable then delete it — free slots recomputed each time."""
        tt = self._save_active_timetable()

        updated = ProgrammeTimetableManager.mutate_timetable_slot(
            timetable=tt,
            action="add",
            slot_data={
                "day": "Tuesday",
                "start_time": "09:00",
                "end_time": "12:00",
                "unit_code": "MAT 201",
                "unit_name": "Linear Algebra",
                "venue": "Hall B",
                "session_type": "Lecture",
            },
            user=self.user,
        )
        sched = updated.schedule_data.get("weekly_schedule", [])
        mat_slots = [s for s in sched if s.get("unit_code") == "MAT 201"]
        self.assertEqual(len(mat_slots), 1)
        slot_id = mat_slots[0]["id"]

        deleted = ProgrammeTimetableManager.mutate_timetable_slot(
            timetable=updated, action="delete", slot_id=slot_id, user=self.user,
        )
        sched_after = deleted.schedule_data.get("weekly_schedule", [])
        self.assertFalse(any(s.get("id") == slot_id for s in sched_after))

    def test_upload_timetable_view(self):
        """
        POST to upload_timetable_view responds immediately with processing=True.
        Celery dispatch is mocked so no worker is needed.
        """
        url = reverse("documents:academic:timetable_upload")
        with patch(
            'documents.tasks.timetable.process_timetable_image_task.apply_async'
        ):
            response = self.client.post(
                url,
                {
                    "image": self._create_sample_image(),
                    "programme_id": self.programme.id,
                    "academic_level": self.academic_level.level,
                    "semester": self.semester.id,
                },
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["revision_number"], 1)
        self.assertTrue(data["processing"])           # async — not yet complete
        self.assertIn("timetable_id", data)
        self.assertEqual(data["status"], TimetableStatus.PROCESSING)

    def test_active_timetable_api(self):
        """GET /timetable/active/ returns the active cohort timetable."""
        self._save_active_timetable()
        url = reverse("documents:academic:timetable_active")
        response = self.client.get(
            url, {"programme_id": self.programme.id, "level": self.academic_level.level},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["active"])
        self.assertIsNotNone(data["timetable"])
        self.assertEqual(data["timetable"]["programme"], self.programme.code)

    def test_timetable_status_api(self):
        """GET /timetable/<id>/status/ returns status and schedule_data when ACTIVE."""
        tt = self._save_active_timetable()
        url = reverse("documents:academic:timetable_status", kwargs={"timetable_id": tt.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["found"])
        self.assertEqual(data["status"], TimetableStatus.ACTIVE)
        self.assertTrue(data["is_active"])
        self.assertIn("schedule_data", data)

    def test_timetable_status_api_htmx_processing_returns_html_spinner(self):
        """HTMX GET /timetable/<id>/status/ when PROCESSING returns HTML section with spinner (never raw JSON)."""
        with patch('documents.tasks.timetable.process_timetable_image_task.apply_async'):
            tt = ProgrammeTimetableManager.save_timetable_image(
                user=self.user,
                image_file=self._create_sample_image(),
                institution=self.institution,
                programme=self.programme,
                academic_level=self.academic_level,
                semester=self.semester,
            )
        url = reverse("documents:academic:timetable_status", kwargs={"timetable_id": tt.id})
        response = self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Spark AI is analyzing your timetable", content)
        self.assertIn("spinner-border", content)
        self.assertNotIn('"found": true', content)

    def test_horizontal_timetable_layout_adaptive_bounds(self):
        """build_week_grid dynamically adapts axis bounds and produces day_rows with lecturer/venue."""
        tt = self._save_active_timetable()
        from documents.academic.workspace import build_week_grid
        grid = build_week_grid(tt.schedule_data)
        self.assertIn("day_rows", grid)
        self.assertIn("time_headers", grid)
        self.assertTrue(len(grid["time_headers"]) > 0)
        # Check Monday row has slots with lecturer and venue
        mon = next(r for r in grid["day_rows"] if r["name"] == "Monday")
        self.assertTrue(len(mon["slots"]) > 0)
        self.assertEqual(mon["slots"][0]["unit_code"], "CSC 221")
        self.assertEqual(mon["slots"][0]["venue"], "LH1")

    def test_spark_context_formatting(self):
        """StudentContext formats <weekly_timetable> XML with both lecture and free slots."""
        sched_data = {
            "title": "Semester 1 Timetable",
            "weekly_schedule": [
                {
                    "day": "Monday", "start_time": "08:00", "end_time": "11:00",
                    "unit_code": "CSC 221", "unit_name": "Database Systems",
                    "venue": "LH1", "session_type": "Lecture",
                }
            ],
            "free_slots": [
                {"day": "Monday", "start_time": "11:00", "end_time": "17:00", "duration_hours": 6.0}
            ],
        }
        ctx = StudentContext(
            programme_name="BSc. Computer Science",
            level="Year 2",
            weekly_timetable=sched_data,
        )
        formatted = ctx.format_context_block()
        self.assertIn("<weekly_timetable>", formatted)
        self.assertIn("CSC 221 Database Systems (LH1) [Lecture]", formatted)
        self.assertIn("Free Study & Revision Slots:", formatted)
        self.assertIn("Monday: 11:00-17:00 (6.0h free)", formatted)
        self.assertIn("</weekly_timetable>", formatted)

    def test_student_timetable_tool(self):
        """StudentTimetableTool can query today's schedule, free slots, and weekly overview."""
        self._save_active_timetable()
        tool = StudentTimetableTool()

        result_today = tool.execute(user=self.user, action="get_today_schedule")
        self.assertTrue(result_today.success)
        self.assertTrue(result_today.data.get("has_timetable"))

        result_free = tool.execute(user=self.user, action="find_free_slots")
        self.assertTrue(result_free.success)
        self.assertIn("free_slots", result_free.data)

        result_weekly = tool.execute(user=self.user, action="get_weekly_schedule")
        self.assertTrue(result_weekly.success)
        self.assertEqual(result_weekly.data.get("programme"), self.programme.code)

    def test_spark_retains_valid_schedule_during_replacement_and_failure(self):
        old = self._save_active_timetable()
        with patch('documents.tasks.timetable.process_timetable_image_task.apply_async'):
            pending = ProgrammeTimetableManager.save_timetable_image(user=self.user,
                image_file=self._create_sample_image(), institution=self.institution,
                programme=self.programme, academic_level=self.academic_level, semester=self.semester)
        old.refresh_from_db()
        self.assertTrue(old.is_active)
        current = ProgrammeTimetableManager.get_active_timetable(institution=self.institution,
            programme=self.programme, academic_level=self.academic_level,
            semester=self.semester, include_processing=False)
        self.assertEqual(current.pk, old.pk)
        from documents.tasks.timetable import _mark_failed
        _mark_failed(pending)
        self.assertTrue(StudentTimetableTool().execute(user=self.user, action='get_weekly_schedule').data['has_timetable'])

    def test_late_older_extraction_cannot_replace_newer_schedule(self):
        first = self._save_active_timetable()
        latest = self._save_active_timetable()
        activated = ProgrammeTimetableManager.activate_timetable(first, SAMPLE_SCHEDULE)
        self.assertFalse(activated)
        latest.refresh_from_db()
        self.assertTrue(latest.is_active)
        self.assertEqual(first.status, TimetableStatus.SUPERSEDED)

    def test_other_semester_schedule_is_not_used_as_current_schedule(self):
        self._save_active_timetable()
        other, _ = Semester.objects.get_or_create(academic_year=self.academic_year, number=2,
            defaults={'start_date': self.semester.start_date, 'end_date': self.semester.end_date})
        result = ProgrammeTimetableManager.get_active_timetable(institution=self.institution,
            programme=self.programme, academic_level=self.academic_level,
            semester=other, include_processing=False)
        self.assertIsNone(result)

    @patch('documents.academic.timetable_service.TimetableExtractionService._resolve_ocr_chain', return_value=[])
    def test_failed_extraction_does_not_invent_a_schedule(self, _):
        with self.assertRaises(ValueError):
            TimetableExtractionService.extract_from_image(b'invalid-image')

    def test_invalid_schedule_times_cannot_activate(self):
        import copy
        from documents.academic.timetable_service import validate_schedule_data
        invalid = copy.deepcopy(SAMPLE_SCHEDULE)
        invalid['weekly_schedule'][0]['end_time'] = '07:00'
        with self.assertRaises(ValueError):
            validate_schedule_data(invalid)

    def test_health_command_quarantines_only_known_placeholder_data(self):
        import io, json
        from django.core.management import call_command
        timetable = self._save_active_timetable()
        fake = {'weekly_schedule': [
            {'id': 'mon_0800_session1', 'unit_code': 'LEC 101'},
            {'id': 'wed_1000_session2', 'unit_code': 'LEC 102'},
            {'id': 'fri_1400_session3', 'unit_code': 'PRA 103'},
        ]}
        ProgrammeTimetable.objects.filter(pk=timetable.pk).update(schedule_data=fake)
        output = io.StringIO()
        call_command('spark_health', quarantine_placeholders=True, stdout=output)
        self.assertEqual(json.loads(output.getvalue())['quarantined_placeholders'], 1)
        timetable.refresh_from_db()
        self.assertFalse(timetable.is_active)
        self.assertEqual(timetable.schedule_data, fake)
        self.assertTrue(timetable.source_image.name)

    def test_saved_timetable_reaches_spark_on_later_context_builds(self):
        from pwanimate.context.user_context import UserContextService
        from pwanimate.context.engine import ContextEngine
        from pwanimate.context.types import IntentCategory
        timetable = self._save_active_timetable()
        first = UserContextService().build(self.user)
        self.assertEqual(first.weekly_timetable['weekly_schedule'][0]['unit_code'], 'CSC 221')
        ProgrammeTimetableManager.mutate_timetable_slot(timetable=timetable, action='update',
            slot_id='mon_0800_csc221', slot_data={'venue': 'New Hall'}, user=self.user)
        later = UserContextService().build(self.user)
        selected = ContextEngine().build_student_context(later, IntentCategory.MIXED, 'What classes do I have today?')
        self.assertIn('New Hall', selected.format_context_block())

    def test_free_slots_remain_inside_the_study_day(self):
        slots = compute_free_slots([{'day': 'Monday', 'start_time': '18:00', 'end_time': '20:00'}])
        monday = [slot for slot in slots if slot['day'] == 'Monday']
        self.assertEqual(monday[0]['start_time'], '08:00')
        self.assertEqual(monday[0]['end_time'], '17:00')
