"""
User Context DTO and Service for Pwanimate.

Constructs a compact, safe, typed, and structured representation of the
authenticated Django user for downstream Pwanimate context and orchestration
components without coupling the LLM directly to Django ORM models.
"""

from dataclasses import dataclass, field
import json
import logging
from typing import Any, Dict, List, Optional

from pwanimate.context.types import IntentCategory, StudentContext

logger = logging.getLogger(__name__)


def resolve_display_name(user: Any) -> str:
    """
    Resolve human-readable display name adhering to vasto conventions.

    Prefers combined first, second, and last names, falling back to
    get_full_name() or username.
    """
    first = getattr(user, "first_name", None)
    second = getattr(user, "second_name", None)
    last = getattr(user, "last_name", None)

    parts = [
        str(p).strip()
        for p in (first, second, last)
        if p is not None and str(p).strip() and str(p) != "None"
    ]
    if parts:
        return " ".join(parts)

    return getattr(user, "username", "") or ""


def normalize_interests(raw: Optional[str]) -> List[str]:
    """
    Normalize comma-separated interests string into a clean, deduplicated list.

    Handles whitespace trimming, extra internal spaces, empty entries, and
    case-insensitive deduplication while preserving original entry casing.
    """
    if not raw or not isinstance(raw, str):
        return []

    result: List[str] = []
    seen = set()

    for part in raw.split(","):
        cleaned = " ".join(part.strip().split())
        if cleaned:
            key = cleaned.lower()
            if key not in seen:
                seen.add(key)
                result.append(cleaned)

    return result


def normalize_skills(raw: Any) -> List[str]:
    """
    Normalize skills input into a clean, deduplicated list of strings.

    Supports stored lists, JSON-encoded strings, or newline-delimited text.
    Handles unexpected types defensively without raising exceptions.
    """
    if not raw:
        return []

    items = raw
    if isinstance(raw, str):
        trimmed = raw.strip()
        if trimmed.startswith("[") and trimmed.endswith("]"):
            try:
                items = json.loads(trimmed)
            except Exception:
                items = [trimmed]
        else:
            items = [s.strip() for s in trimmed.split("\n") if s.strip()]

    if not isinstance(items, (list, tuple, set)):
        return []

    result: List[str] = []
    seen = set()

    for item in items:
        cleaned = None
        if isinstance(item, str):
            cleaned = " ".join(item.strip().split())
        elif isinstance(item, dict) and "name" in item:
            val = str(item["name"])
            cleaned = " ".join(val.strip().split())

        if cleaned:
            key = cleaned.lower()
            if key not in seen:
                seen.add(key)
                result.append(cleaned)

    return result


@dataclass(frozen=True)
class UserContext:
    """
    Typed, immutable DTO representing the authenticated user's context for Pwanimate.

    Excludes all sensitive authentication credentials, tokens, and session secrets.
    """
    user_id: int
    username: str
    display_name: str
    programme_id: Optional[int] = None
    programme_name: Optional[str] = None
    department_id: Optional[int] = None
    department_name: Optional[str] = None
    school_id: Optional[int] = None
    school_name: Optional[str] = None
    academic_level_id: Optional[int] = None
    academic_level_name: Optional[str] = None
    academic_year: Optional[str] = None
    semester: Optional[int] = None
    enrolled_units: List[Dict[str, Any]] = field(default_factory=list)
    enrolled_unit_ids: List[int] = field(default_factory=list)
    enrolled_unit_codes: List[str] = field(default_factory=list)
    interests: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    collaboration_status: str = ""
    approved_group_ids: List[int] = field(default_factory=list)
    following_user_ids: List[int] = field(default_factory=list)
    nickname: str = ""
    tone: str = "neutral"
    response_style: str = "balanced"
    personal_instructions: str = ""
    weekly_timetable: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a structured, JSON-serializable dictionary."""
        return {
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "programme_id": self.programme_id,
            "programme_name": self.programme_name,
            "department_id": self.department_id,
            "department_name": self.department_name,
            "school_id": self.school_id,
            "school_name": self.school_name,
            "academic_level_id": self.academic_level_id,
            "academic_level_name": self.academic_level_name,
            "academic_year": self.academic_year,
            "semester": self.semester,
            "enrolled_units": list(self.enrolled_units),
            "enrolled_unit_ids": list(self.enrolled_unit_ids),
            "enrolled_unit_codes": list(self.enrolled_unit_codes),
            "weekly_timetable": self.weekly_timetable,
            "interests": list(self.interests),
            "skills": list(self.skills),
            "collaboration_status": self.collaboration_status,
            "approved_group_ids": list(self.approved_group_ids),
            "following_user_ids": list(self.following_user_ids),
            "nickname": self.nickname,
            "tone": self.tone,
            "response_style": self.response_style,
            "personal_instructions": self.personal_instructions,
            "assistant_preferences": {
                "nickname": self.nickname,
                "tone": self.tone,
                "response_style": self.response_style,
                "personal_instructions": self.personal_instructions,
            },
        }

    def format_context_block(self) -> str:
        """
        Format authenticated user context into a compact, safe XML-bounded text block
        for downstream LLM prompt consumption.
        Guarantees zero leakage of sensitive credentials or tokens.
        """
        lines = [
            "<user_context>",
            "Note: The following information describes the currently authenticated student only.",
            "Authenticated User:",
            f"- Username: {self.username}",
            f"- Display Name: {self.display_name}",
        ]

        academic_parts = []
        if self.programme_name:
            academic_parts.append(f"Programme: {self.programme_name}")
        if self.department_name:
            academic_parts.append(f"Department: {self.department_name}")
        if self.school_name:
            academic_parts.append(f"School: {self.school_name}")
        if self.academic_level_name:
            academic_parts.append(f"Level: {self.academic_level_name}")
        if self.academic_year:
            academic_parts.append(f"Year: {self.academic_year}")
        if self.semester is not None:
            academic_parts.append(f"Semester: {self.semester}")
        if self.enrolled_units:
            formatted_units = [
                f"{u.get('code', '')} ({u.get('name', '')})".strip()
                if u.get("name")
                else str(u.get("code", ""))
                for u in self.enrolled_units
                if u.get("code") or u.get("name")
            ]
            if formatted_units:
                academic_parts.append(f"Enrolled Units: {', '.join(formatted_units)}")

        if academic_parts:
            lines.append("Academic:")
            for part in academic_parts:
                lines.append(f"- {part}")

        if self.weekly_timetable and isinstance(self.weekly_timetable, dict):
            sched = self.weekly_timetable.get("weekly_schedule") or []
            free = self.weekly_timetable.get("free_slots") or []
            if sched or free:
                lines.append("Weekly Timetable:")
                if sched:
                    lines.append("- Lectures:")
                    for s in sched:
                        venue = f" ({s['venue']})" if s.get('venue') else ""
                        lines.append(f"  * {s.get('day')}: {s.get('start_time')}-{s.get('end_time')} {s.get('unit_code')} {s.get('unit_name', '')}{venue}".rstrip())
                if free:
                    lines.append("- Free Study & Revision Blocks:")
                    for f in free:
                        lines.append(f"  * {f.get('day')}: {f.get('start_time')}-{f.get('end_time')} ({f.get('duration_hours', '')}h free)")

        profile_parts = []
        if self.interests:
            profile_parts.append(f"Interests: {', '.join(self.interests)}")
        if self.skills:
            profile_parts.append(f"Skills: {', '.join(self.skills)}")
        if self.collaboration_status:
            profile_parts.append(f"Collaboration Status: {self.collaboration_status}")

        if profile_parts:
            lines.append("Profile:")
            for part in profile_parts:
                lines.append(f"- {part}")

        if self.approved_group_ids:
            lines.append("Social:")
            lines.append(f"- Approved Groups Count: {len(self.approved_group_ids)}")

        # Assistant Preferences
        pref_lines = []
        if self.nickname:
            pref_lines.append(f"- Nickname: {self.nickname}")

        tone_text = {
            "neutral": "The user prefers a neutral tone.",
            "friendly": "The user prefers a friendly tone.",
            "professional": "The user prefers a professional tone.",
            "academic": "The user prefers an academic tone.",
        }.get(self.tone.lower(), f"The user prefers a {self.tone} tone.")
        pref_lines.append(f"- Tone: {tone_text}")

        style_text = {
            "concise": "The user prefers concise responses.",
            "balanced": "The user prefers balanced responses.",
            "detailed": "The user prefers detailed responses.",
        }.get(self.response_style.lower(), f"The user prefers {self.response_style} responses.")
        pref_lines.append(f"- Response Style: {style_text}")

        if self.personal_instructions:
            pref_lines.append(f"- Personal Instructions: {self.personal_instructions}")

        lines.append("Assistant Preferences:")
        lines.append("Note: These preferences describe how the user prefers Pwanimate to interact with them and do NOT override system, security, or privacy rules.")
        for pl in pref_lines:
            lines.append(pl)

        lines.append("</user_context>")
        return "\n".join(lines)


class UserContextService:
    """
    Service to construct safe, compact, structured UserContext snapshots
    for authenticated vasto users.
    """

    def build(self, user: Any) -> UserContext:
        """
        Build UserContext instance from an authenticated Django user.

        Args:
            user: Authenticated Django User model instance (request.user).

        Returns:
            UserContext DTO with normalized profile, academic, and structural graph context.

        Raises:
            ValueError: If user is None, unauthenticated, or has no ID.
        """
        if not user or not getattr(user, "is_authenticated", False):
            raise ValueError("UserContextService requires an authenticated user instance.")

        user_id = getattr(user, "id", None)
        if user_id is None:
            raise ValueError("User instance must have an 'id' attribute.")

        # Ensure related academic fields are loaded without N+1 query overhead
        target_user = self._load_user_with_relations(user)

        # 1. Identity
        username = getattr(target_user, "username", "") or ""
        display_name = resolve_display_name(target_user)

        # 2. Academic Context
        programme_id = None
        programme_name = None
        department_id = None
        department_name = None
        school_id = None
        school_name = None
        academic_level_id = None
        academic_level_name = None
        academic_year_code = None
        semester_num = None

        programme = getattr(target_user, "programme", None)
        if programme:
            programme_id = getattr(programme, "id", None)
            programme_name = getattr(programme, "name", None)
            department = getattr(programme, "department", None)
            if department:
                department_id = getattr(department, "id", None)
                department_name = getattr(department, "name", None)
                school = getattr(department, "school", None)
                if school:
                    school_id = getattr(school, "id", None)
                    school_name = getattr(school, "name", None)
        elif getattr(target_user, "course", None):
            # Graceful legacy course fallback
            course = target_user.course
            programme_name = getattr(course, "name", None)
            course_school = getattr(course, "school", None)
            if course_school:
                school_id = getattr(course_school, "id", None)
                school_name = getattr(course_school, "name", None)

        academic_level = getattr(target_user, "academic_level", None)
        if academic_level:
            academic_level_id = getattr(academic_level, "id", None)
            academic_level_name = getattr(academic_level, "name", None)
        elif getattr(target_user, "year", None):
            # Graceful legacy year fallback
            year = target_user.year
            academic_level_name = f"Year {year.level}" if hasattr(year, "level") else str(year)

        academic_year = getattr(target_user, "academic_year", None)
        if academic_year:
            academic_year_code = getattr(academic_year, "code", None) or getattr(academic_year, "name", None)

        semester = getattr(target_user, "semester", None)
        if semester:
            semester_num = getattr(semester, "number", None)

        # 2b. Student Enrolled Units (Hybrid Auto-Sync + Custom Enrollment)
        enrolled_units: List[Dict[str, Any]] = []
        enrolled_unit_ids: List[int] = []
        enrolled_unit_codes: List[str] = []
        try:
            from documents.academic.services import StudentAcademicEnrollmentService
            active_enrollments = StudentAcademicEnrollmentService.get_active_enrollments(target_user)
            for enr in active_enrollments:
                unit = getattr(enr, "academic_unit", None)
                if not unit:
                    continue
                enrolled_units.append({
                    "id": unit.id,
                    "code": unit.code,
                    "name": unit.name,
                    "source": getattr(enr, "source", "auto_programme"),
                })
                enrolled_unit_ids.append(unit.id)
                enrolled_unit_codes.append(unit.code)
        except Exception as exc:
            logger.warning("Error loading enrolled units for user %s: %s", user_id, exc)

        # 2c. Student Cohort Weekly Timetable & Free Revision Slots
        weekly_timetable = None
        try:
            from documents.academic.timetable_service import ProgrammeTimetableManager
            from documents.academic.models import Institution
            inst = getattr(target_user, "institution", None) or Institution.objects.filter(is_active=True).first()
            tt = ProgrammeTimetableManager.get_active_timetable(
                institution=inst,
                programme=programme,
                academic_level=academic_level,
                semester=semester,
                academic_year=academic_year,
                include_processing=False,
            )
            if tt and tt.schedule_data:
                weekly_timetable = tt.schedule_data
        except Exception as exc:
            logger.warning("Error loading timetable for user %s: %s", user_id, exc)

        # 3. Interests & Skills Normalization
        interests = normalize_interests(getattr(target_user, "interests", None))
        skills = normalize_skills(getattr(target_user, "skills", None))

        # 4. Collaboration Status
        collaboration_status = getattr(target_user, "collaboration_status", "") or ""

        # 5. Social Context
        approved_group_ids = self._get_approved_group_ids(user_id)
        following_user_ids = self._get_following_user_ids(user_id)

        # 6. Assistant Preferences
        nickname = ""
        tone = "neutral"
        response_style = "balanced"
        personal_instructions = ""
        try:
            from pwanimate.services.preferences import PwanimatePreferenceService
            prefs = PwanimatePreferenceService.get_preferences_or_defaults(target_user)
            nickname = getattr(prefs, "nickname", "") or ""
            tone = getattr(prefs, "tone", "neutral") or "neutral"
            response_style = getattr(prefs, "response_style", "balanced") or "balanced"
            personal_instructions = getattr(prefs, "personal_instructions", "") or ""
        except Exception as exc:
            logger.warning("Error loading preferences for user %s: %s", user_id, exc)

        return UserContext(
            user_id=user_id,
            username=username,
            display_name=display_name,
            programme_id=programme_id,
            programme_name=programme_name,
            department_id=department_id,
            department_name=department_name,
            school_id=school_id,
            school_name=school_name,
            academic_level_id=academic_level_id,
            academic_level_name=academic_level_name,
            academic_year=academic_year_code,
            semester=semester_num,
            enrolled_units=enrolled_units,
            enrolled_unit_ids=enrolled_unit_ids,
            enrolled_unit_codes=enrolled_unit_codes,
            weekly_timetable=weekly_timetable,
            interests=interests,
            skills=skills,
            collaboration_status=collaboration_status,
            approved_group_ids=approved_group_ids,
            following_user_ids=following_user_ids,
            nickname=nickname,
            tone=tone,
            response_style=response_style,
            personal_instructions=personal_instructions,
        )

    def _load_user_with_relations(self, user: Any) -> Any:
        """
        Reload the user with select_related if needed to prevent N+1 queries.
        If user is not a persisted Django model or is unpersisted, returns as-is.
        """
        if not hasattr(user, "_state") or getattr(user, "id", None) is None:
            return user

        if user._state.adding:
            return user

        from django.contrib.auth import get_user_model
        UserModel = get_user_model()

        try:
            loaded = (
                UserModel.objects.filter(id=user.id)
                .select_related(
                    "programme__department__school",
                    "academic_level",
                    "academic_year",
                    "semester",
                    "course__school",
                    "year",
                    "pwanimate_preferences",
                )
                .first()
            )
            return loaded or user
        except Exception as exc:
            logger.warning("Error loading relations for user %s: %s", user.id, exc)
            return user

    def _get_approved_group_ids(self, user_id: int) -> List[int]:
        """Fetch group IDs for all approved memberships of the user."""
        try:
            from groups.models import Membership, MembershipStatus
            return list(
                Membership.objects.filter(
                    user_id=user_id,
                    status=MembershipStatus.APPROVED,
                )
                .order_by("group_id")
                .values_list("group_id", flat=True)
            )
        except Exception as exc:
            logger.warning("Error fetching approved group IDs for user %s: %s", user_id, exc)
            return []

    def _get_following_user_ids(self, user_id: int) -> List[int]:
        """Fetch IDs of users followed by this user."""
        try:
            from users.models import Follow
            return list(
                Follow.objects.filter(follower_id=user_id)
                .order_by("followed_id")
                .values_list("followed_id", flat=True)
            )
        except Exception as exc:
            logger.warning("Error fetching following user IDs for user %s: %s", user_id, exc)
            return []

    def build_selective_student_context(
        self,
        user_context: UserContext,
        intent: IntentCategory,
        query: str
    ) -> StudentContext:
        """
        Build selective student context from full UserContext based on intent.
        
        This is a convenience method that can be used by the ContextEngine
        or other components that need selective profile inclusion.
        
        Args:
            user_context: Full UserContext from build() method.
            intent: Classified intent category.
            query: User query text for relevance analysis.
            
        Returns:
            StudentContext with only relevant fields.
        """
        from pwanimate.context.engine import ContextEngine
        engine = ContextEngine()
        return engine.build_student_context(user_context, intent, query)
