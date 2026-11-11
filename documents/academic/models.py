"""Academic domain models for university structure.

These models represent the hierarchical academic structure of institutions
and are designed to be reusable across vasto modules.

Hierarchy:
Institution → Faculty → School → Department → Programme → Academic Unit
(Calendar: Academic Year → Semester; Progression: Academic Level)
Institution scope is derived through the hierarchy (no denormalised copies).
"""

from django.conf import settings
from django.db import models
from django.core.validators import RegexValidator
from django.utils.text import slugify


class InstitutionType(models.TextChoices):
    UNIVERSITY = 'university', 'University'
    COLLEGE = 'college', 'College'
    POLYTECHNIC = 'polytechnic', 'Polytechnic'
    OTHER = 'other', 'Other'


class Institution(models.Model):
    """A university or other educational institution owning an academic catalogue."""

    name = models.CharField(
        max_length=200,
        unique=True,
        help_text="Official name of the institution (e.g., 'Pwani University')"
    )
    short_name = models.CharField(
        max_length=20,
        blank=True,
        help_text="Short code or abbreviation (e.g., 'PU')"
    )
    slug = models.SlugField(
        max_length=250,
        unique=True,
        help_text="URL-friendly slug"
    )
    institution_type = models.CharField(
        max_length=20,
        choices=InstitutionType.choices,
        default=InstitutionType.UNIVERSITY,
        db_index=True,
        help_text="Type of institution"
    )
    country = models.CharField(max_length=100, default='Kenya', blank=True)
    county_or_region = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)
    website = models.URLField(blank=True)
    logo = models.ImageField(upload_to='institutions/logos/', null=True, blank=True)
    cover_image = models.ImageField(upload_to='institutions/covers/', null=True, blank=True)
    brand_color = models.CharField(max_length=7, default='#0d6efd', blank=True)
    email_domains = models.CharField(
        max_length=255,
        blank=True,
        help_text="Comma-separated email domains (e.g., 'pu.ac.ke, student.pu.ac.ke')"
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Whether this institution is available for student setup"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = "Institution"
        verbose_name_plural = "Institutions"
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['is_active']),
            models.Index(fields=['name']),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class AcademicYear(models.Model):
    """Represents an academic year (e.g., 2024/2025)."""
    
    code = models.CharField(
        max_length=20,
        unique=True,
        help_text="Unique identifier for the academic year (e.g., '2024/2025')"
    )
    name = models.CharField(
        max_length=50,
        help_text="Human-readable name (e.g., '2024/2025')"
    )
    start_date = models.DateField(help_text="Start date of the academic year")
    end_date = models.DateField(help_text="End date of the academic year")
    is_current = models.BooleanField(
        default=False,
        help_text="Whether this is the current academic year"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-start_date']
        verbose_name = "Academic Year"
        verbose_name_plural = "Academic Years"
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['is_current']),
        ]
    
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        # Ensure only one current academic year
        if self.is_current:
            AcademicYear.objects.filter(is_current=True).update(is_current=False)
        super().save(*args, **kwargs)


class AcademicLevel(models.Model):
    """Represents a student's academic level (Year 1, Year 2, etc.).
    
    This is a reusable entity independent of any specific programme.
    It represents the progression level in a student's academic journey.
    """
    
    LEVEL_CHOICES = [
        (1, 'Year 1'),
        (2, 'Year 2'),
        (3, 'Year 3'),
        (4, 'Year 4'),
        (5, 'Year 5'),
        (6, 'Year 6'),
        (7, 'Masters'),
        (8, 'PhD'),
    ]
    
    level = models.PositiveSmallIntegerField(
        choices=LEVEL_CHOICES,
        unique=True,
        help_text="Academic level number (1-8)"
    )
    name = models.CharField(
        max_length=50,
        unique=True,
        help_text="Human-readable level name"
    )
    description = models.TextField(
        blank=True,
        help_text="Description of this academic level"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this level is currently active"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['level']
        verbose_name = "Academic Level"
        verbose_name_plural = "Academic Levels"
        indexes = [
            models.Index(fields=['level']),
            models.Index(fields=['is_active']),
        ]
    
    def __str__(self):
        return self.name


class Semester(models.Model):
    """Represents a semester within an academic year."""
    
    SEMESTER_CHOICES = [
        (1, 'Semester 1'),
        (2, 'Semester 2'),
        (3, 'Semester 3'),
    ]
    
    number = models.PositiveSmallIntegerField(
        choices=SEMESTER_CHOICES,
        help_text="Semester number (1, 2, or 3)"
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.CASCADE,
        related_name='semesters',
        help_text="The academic year this semester belongs to"
    )
    start_date = models.DateField(help_text="Start date of the semester")
    end_date = models.DateField(help_text="End date of the semester")
    is_current = models.BooleanField(
        default=False,
        help_text="Whether this is the current semester"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['academic_year', 'number']
        unique_together = ['number', 'academic_year']
        verbose_name = "Semester"
        verbose_name_plural = "Semesters"
        indexes = [
            models.Index(fields=['academic_year', 'number']),
            models.Index(fields=['is_current']),
        ]
    
    def __str__(self):
        return f"{self.get_number_display()} {self.academic_year.code}"
    
    def save(self, *args, **kwargs):
        # Ensure only one current semester
        if self.is_current:
            Semester.objects.filter(is_current=True).update(is_current=False)
        super().save(*args, **kwargs)


class Faculty(models.Model):
    """Represents a faculty within an institution (e.g., Faculty of Science)."""

    institution = models.ForeignKey(
        Institution,
        on_delete=models.CASCADE,
        related_name='faculties',
        null=True,
        blank=True,
        db_index=True,
        help_text="The institution this faculty belongs to (null = legacy/unmapped catalogue)"
    )
    code = models.CharField(
        max_length=20,
        unique=True,
        validators=[RegexValidator(r'^[A-Z]{2,10}$', 'Code must be uppercase letters only')],
        help_text="Unique faculty code (e.g., 'SCI')"
    )
    name = models.CharField(
        max_length=200,
        help_text="Full name of the faculty"
    )
    slug = models.SlugField(
        max_length=250,
        unique=True,
        help_text="URL-friendly slug"
    )
    description = models.TextField(
        blank=True,
        help_text="Description of the faculty"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['code']
        verbose_name = "Faculty"
        verbose_name_plural = "Faculties"
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['slug']),
            models.Index(fields=['institution']),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def get_institution(self):
        """Return the owning institution (or None for legacy unmapped rows)."""
        return self.institution

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class School(models.Model):
    """Represents a school within a faculty (e.g., School of Computing)."""
    
    code = models.CharField(
        max_length=20,
        unique=True,
        validators=[RegexValidator(r'^[A-Z]{2,10}$', 'Code must be uppercase letters only')],
        help_text="Unique school code (e.g., 'COMP')"
    )
    name = models.CharField(
        max_length=200,
        help_text="Full name of the school"
    )
    slug = models.SlugField(
        max_length=250,
        unique=True,
        help_text="URL-friendly slug"
    )
    institution = models.ForeignKey(
        Institution,
        on_delete=models.CASCADE,
        related_name='schools',
        null=True,
        blank=True,
        db_index=True,
        help_text="The institution this school belongs to (optional if faculty is set)"
    )
    faculty = models.ForeignKey(
        Faculty,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='schools',
        help_text="The faculty this school belongs to"
    )
    description = models.TextField(
        blank=True,
        help_text="Description of the school"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['code']
        verbose_name = "School"
        verbose_name_plural = "Schools"
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['slug']),
            models.Index(fields=['faculty']),
            models.Index(fields=['institution']),
        ]
    
    def __str__(self):
        return f"{self.code} - {self.name}"

    def get_institution(self):
        """Derive the owning institution (directly or via faculty)."""
        if getattr(self, 'institution_id', None):
            return self.institution
        if getattr(self, 'faculty_id', None) and self.faculty:
            return self.faculty.get_institution()
        return None

    def save(self, *args, **kwargs):
        if getattr(self, 'faculty_id', None) and not getattr(self, 'institution_id', None):
            self.institution = self.faculty.institution
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Department(models.Model):
    """Represents a department within a school."""
    
    code = models.CharField(
        max_length=20,
        unique=True,
        validators=[RegexValidator(r'^[A-Z]{2,10}$', 'Code must be uppercase letters only')],
        help_text="Unique department code (e.g., 'CS')"
    )
    name = models.CharField(
        max_length=200,
        help_text="Full name of the department"
    )
    slug = models.SlugField(
        max_length=250,
        unique=True,
        help_text="URL-friendly slug"
    )
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name='departments',
        help_text="The school this department belongs to"
    )
    description = models.TextField(
        blank=True,
        help_text="Description of the department"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['code']
        verbose_name = "Department"
        verbose_name_plural = "Departments"
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['slug']),
            models.Index(fields=['school']),
        ]
    
    def __str__(self):
        return f"{self.code} - {self.name}"

    def get_institution(self):
        """Derive the owning institution via school."""
        if getattr(self, 'school_id', None) and self.school:
            return self.school.get_institution()
        return None

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Programme(models.Model):
    """Represents an academic programme (e.g., BSc Computer Science)."""
    
    code = models.CharField(
        max_length=20,
        unique=True,
        validators=[RegexValidator(r'^[A-Z0-9]{2,20}$', 'Code must be alphanumeric uppercase')],
        help_text="Unique programme code (e.g., 'BSC-CS')"
    )
    name = models.CharField(
        max_length=200,
        help_text="Full name of the programme"
    )
    slug = models.SlugField(
        max_length=250,
        unique=True,
        help_text="URL-friendly slug"
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.CASCADE,
        related_name='programmes',
        help_text="The department this programme belongs to"
    )
    degree_type = models.CharField(
        max_length=50,
        help_text="Type of degree (e.g., 'Bachelor', 'Master', 'PhD')"
    )
    duration_years = models.PositiveSmallIntegerField(
        help_text="Duration of the programme in years"
    )
    description = models.TextField(
        blank=True,
        help_text="Description of the programme"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this programme is currently active"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['code']
        verbose_name = "Programme"
        verbose_name_plural = "Programmes"
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['slug']),
            models.Index(fields=['department']),
            models.Index(fields=['is_active']),
        ]
    
    def __str__(self):
        return f"{self.code} - {self.name}"

    def get_institution(self):
        """Derive the owning institution via department → school."""
        if getattr(self, 'department_id', None) and self.department:
            return self.department.get_institution()
        return None

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class AcademicUnit(models.Model):
    """Represents an academic unit/course (e.g., CSC221 Database Systems).
    
    This is a shared entity that can belong to multiple programmes.
    Use junction tables to map programmes to units.
    """
    
    code = models.CharField(
        max_length=20,
        unique=True,
        validators=[RegexValidator(r'^[A-Z]{3} [A-Z]\d{3}$', "Code must be like 'SCS B101'")],
        help_text="Unique unit code (e.g., 'SCS B101')"
    )
    name = models.CharField(
        max_length=200,
        help_text="Full name of the academic unit"
    )
    slug = models.SlugField(
        max_length=250,
        unique=True,
        help_text="URL-friendly slug"
    )
    description = models.TextField(
        blank=True,
        help_text="Description of the academic unit"
    )
    credit_hours = models.PositiveSmallIntegerField(
        default=3,
        help_text="Credit hours for this unit"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this unit is currently active"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['code']
        verbose_name = "Academic Unit"
        verbose_name_plural = "Academic Units"
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['slug']),
            models.Index(fields=['is_active']),
        ]
    
    def __str__(self):
        return f"{self.code} - {self.name}"
    
    @staticmethod
    def format_canonical_code(raw_code: str) -> str:
        import re
        compact = re.sub(r'[\s\-_\.]+', '', str(raw_code or '')).upper()
        m = re.match(r'^([A-Z]{3})([A-Z]\d{3})$', compact)
        if m:
            return f"{m.group(1)} {m.group(2)}"
        return ' '.join(str(raw_code or '').strip().upper().split())

    def clean(self):
        super().clean()
        if self.code:
            self.code = self.format_canonical_code(self.code)
        if self.name:
            self.name = ' '.join(str(self.name).strip().split())

    def save(self, *args, **kwargs):
        if self.code:
            self.code = self.format_canonical_code(self.code)
        if self.name:
            self.name = ' '.join(str(self.name).strip().split())
        if not self.slug:
            self.slug = slugify(f"{self.code} {self.name}")
        super().save(*args, **kwargs)



class ProgrammeUnit(models.Model):
    """Junction table mapping Programmes to Academic Units.
    
    Many programmes can share the same unit, and one programme can have many units.
    This junction table prevents duplicate unit creation and includes curriculum context.
    """
    
    programme = models.ForeignKey(
        Programme,
        on_delete=models.CASCADE,
        related_name='programme_units'
    )
    academic_unit = models.ForeignKey(
        AcademicUnit,
        on_delete=models.CASCADE,
        related_name='programme_units'
    )
    academic_level = models.ForeignKey(
        AcademicLevel,
        on_delete=models.CASCADE,
        related_name='programme_units',
        null=True,
        blank=True,
        help_text="The academic level this unit is offered at"
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.CASCADE,
        related_name='programme_units',
        null=True,
        blank=True,
        help_text="The academic year this unit is offered"
    )
    semester = models.ForeignKey(
        Semester,
        on_delete=models.CASCADE,
        related_name='programme_units',
        help_text="The semester this unit is offered in this programme"
    )
    is_core = models.BooleanField(
        default=True,
        help_text="Whether this is a core unit for the programme"
    )
    is_elective = models.BooleanField(
        default=False,
        help_text="Whether this is an elective unit"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['programme', 'academic_unit', 'academic_level', 'academic_year', 'semester']
        verbose_name = "Programme Unit"
        verbose_name_plural = "Programme Units"
        indexes = [
            models.Index(fields=['programme']),
            models.Index(fields=['academic_unit']),
            models.Index(fields=['academic_level']),
            models.Index(fields=['academic_year']),
            models.Index(fields=['semester']),
            models.Index(fields=['programme', 'academic_level', 'academic_year', 'semester']),
        ]
    
    def __str__(self):
        return f"{self.programme.code} - {self.academic_unit.code} ({self.academic_level}, {self.semester})"


class StudentUnitEnrollment(models.Model):
    """Links an authenticated student to their active AcademicUnits.

    Supports hybrid auto-synchronization from ProgrammeUnit (based on the student's
    programme, academic_level, and semester) alongside custom student selections
    for electives, carry-overs/retakes, or explicit opt-outs.
    """

    ENROLLMENT_SOURCE_CHOICES = [
        ('auto_programme', 'Auto-Synced from Programme & Year'),
        ('custom_elective', 'Student Added (Elective)'),
        ('retake', 'Student Added (Retake / Carry-over)'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='unit_enrollments',
        help_text="The student enrolled in this academic unit",
    )
    academic_unit = models.ForeignKey(
        AcademicUnit,
        on_delete=models.CASCADE,
        related_name='student_enrollments',
        help_text="The academic unit the student is enrolled in",
    )
    academic_level = models.ForeignKey(
        AcademicLevel,
        on_delete=models.SET_NULL,
        related_name='student_unit_enrollments',
        null=True,
        blank=True,
        help_text="Academic level (Year) associated with this enrollment",
    )
    semester = models.ForeignKey(
        Semester,
        on_delete=models.SET_NULL,
        related_name='student_unit_enrollments',
        null=True,
        blank=True,
        help_text="Semester associated with this enrollment",
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.SET_NULL,
        related_name='student_unit_enrollments',
        null=True,
        blank=True,
        help_text="Academic year associated with this enrollment",
    )
    source = models.CharField(
        max_length=20,
        choices=ENROLLMENT_SOURCE_CHOICES,
        default='auto_programme',
        db_index=True,
        help_text="How this unit enrollment was created",
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Whether this unit is currently active for the student",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['academic_unit__code']
        unique_together = ['user', 'academic_unit']
        verbose_name = "Student Unit Enrollment"
        verbose_name_plural = "Student Unit Enrollments"
        indexes = [
            models.Index(fields=['user', 'is_active']),
            models.Index(fields=['academic_unit', 'is_active']),
            models.Index(fields=['user', 'source']),
        ]

    def __str__(self):
        return f"{getattr(self.user, 'username', self.user_id)} -> {self.academic_unit.code} ({'active' if self.is_active else 'inactive'})"


class TimetableStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending Extraction'
    PROCESSING = 'PROCESSING', 'Processing Image'
    ACTIVE = 'ACTIVE', 'Active'
    SUPERSEDED = 'SUPERSEDED', 'Superseded'
    FAILED = 'FAILED', 'Failed'


class TimetableType(models.TextChoices):
    LECTURE = 'LECTURE', 'Weekly Lecture Schedule'
    EXAM = 'EXAM', 'Examination Timetable'
    CAT = 'CAT', 'CAT / Midterm Schedule'


class ProgrammeTimetable(models.Model):
    """Multi-tenant timetable for a specific programme, academic level, and semester."""

    institution = models.ForeignKey(
        Institution,
        on_delete=models.CASCADE,
        related_name='timetables',
        help_text="The institution this timetable belongs to"
    )
    programme = models.ForeignKey(
        Programme,
        on_delete=models.CASCADE,
        related_name='timetables',
        help_text="The academic programme this timetable applies to"
    )
    academic_level = models.ForeignKey(
        AcademicLevel,
        on_delete=models.CASCADE,
        related_name='timetables',
        help_text="The year of study / academic level"
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='timetables',
        help_text="The academic year"
    )
    semester = models.ForeignKey(
        Semester,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='timetables',
        help_text="The semester"
    )
    timetable_type = models.CharField(
        max_length=20,
        choices=TimetableType.choices,
        default=TimetableType.LECTURE,
        db_index=True
    )
    status = models.CharField(
        max_length=20,
        choices=TimetableStatus.choices,
        default=TimetableStatus.ACTIVE,
        db_index=True
    )
    revision_number = models.PositiveSmallIntegerField(
        default=1,
        help_text="Revision number (1 for original, 2 for revised, etc.)"
    )
    title = models.CharField(
        max_length=255,
        blank=True,
        help_text="Human-readable title (e.g. 'Semester 1 Official Lecture Timetable')"
    )
    change_summary = models.TextField(
        blank=True,
        help_text="Summary of changes in this revision"
    )
    source_image = models.ImageField(
        upload_to='timetables/images/%Y/%m/',
        help_text="The uploaded (or cropped) timetable image"
    )
    schedule_data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Structured JSON weekly schedule extracted from the image"
    )
    raw_extracted_text = models.TextField(
        blank=True,
        help_text="Raw OCR or model text output"
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Whether this is the current active timetable for the cohort"
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='uploaded_timetables',
        help_text="Student or administrator who uploaded the timetable"
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='verified_timetables',
        help_text="User (class rep, delegate, or staff) who verified this timetable"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Programme Timetable"
        verbose_name_plural = "Programme Timetables"
        indexes = [
            models.Index(fields=['institution', 'programme', 'academic_level', 'is_active']),
            models.Index(fields=['status']),
            models.Index(fields=['timetable_type']),
        ]

    def __str__(self):
        return f"{self.programme.code} - {self.academic_level.name} (Rev {self.revision_number})"

    def save(self, *args, **kwargs):
        if self.is_active and self.programme_id and self.academic_level_id:
            ProgrammeTimetable.objects.filter(
                institution_id=self.institution_id,
                programme_id=self.programme_id,
                academic_level_id=self.academic_level_id,
                timetable_type=self.timetable_type,
                is_active=True
            ).exclude(pk=self.pk).update(is_active=False, status=TimetableStatus.SUPERSEDED)
        super().save(*args, **kwargs)

    def get_weekly_schedule(self):
        """Return list of weekly schedule slots sorted by day_index and start_time."""
        if isinstance(self.schedule_data, dict):
            return self.schedule_data.get('weekly_schedule', [])
        return []

    def get_free_slots(self):
        """Return list of computed free study blocks."""
        if isinstance(self.schedule_data, dict):
            return self.schedule_data.get('free_slots', [])
        return []


