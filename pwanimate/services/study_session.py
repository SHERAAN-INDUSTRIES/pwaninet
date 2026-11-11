"""
Study Mode Session, Checkpoint, Summary, and Collection Services for Pwanimate.

Implements:
- Prefix-anchored `@study` command parsing.
- Transactional Study Mode session lifecycle (start, continue, pause, resume, end, dismiss banner).
- Single-active-session per user invariant enforcement.
- Context Rail state persistence and permission-revalidated restoration across visits.
- Open-document extraction availability verification ("Open in UI != Available to AI").
- Structured learning checkpoints separating demonstrated mastery from inferred understanding.
- Bounded context reconstruction (latest checkpoint + recent window + selective older-turn retrieval).
- Session summary generation, review, PDF/DOCX export reuse, and Collection saving.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from documents.collections.models import Collection, CollectionItem, CollectionShare
from documents.models import Document
from pwanimate.ai.gateway.gateway import AIGateway
from pwanimate.ai.gateway.types import ChatMessage, LLMRequest
from pwanimate.context.types import estimate_tokens
from pwanimate.models import (
    DocumentChunk,
    PwanimateAttachment,
    PwanimateConversation,
    PwanimateMessage,
    PwanimateStudyCheckpoint,
    PwanimateStudySession,
)
from pwanimate.services.attachment import AttachmentService
from pwanimate.services.conversation import ConversationService

logger = logging.getLogger(__name__)

# Matches leading `@study` only at the beginning of the trimmed message (case-insensitive),
# followed by either end-of-string, whitespace, or colon+whitespace.
STUDY_COMMAND_REGEX = re.compile(r"^\s*@study(?:(?:\s+|:\s*)([\s\S]*)|$)", re.IGNORECASE)

# Passive acknowledgment patterns that must never count as demonstrated mastery
PASSIVE_ACKNOWLEDGMENT_REGEX = re.compile(
    r"^\s*(?:ok(?:ay)?|yes|yeah|yep|sure|thanks|thank you|got it|i see|makes sense|"
    r"understood|i understand|clear|cool|nice|alright|continue|next|go on|proceed|"
    r"ok\s+got\s+it(?:\s+thanks)?|yes\s+thanks|sawa|poa|asante|ndio)[.!?\s]*$",
    re.IGNORECASE,
)

STUDY_HISTORY_MAX_MESSAGES = 8
STUDY_HISTORY_MAX_TOKENS = 1200
STUDY_OLDER_RETRIEVAL_MAX_MESSAGES = 3
STUDY_OLDER_RETRIEVAL_MAX_TOKENS = 350
CHECKPOINT_AUTO_INTERVAL_MESSAGES = 4
CHECKPOINT_LOCK_TIMEOUT_SECONDS = 45


class StudyWarningDict(dict):
    """Dictionary representing a source-availability warning that also supports substring checks against its message."""

    def __contains__(self, item: Any) -> bool:
        if super().__contains__(item):
            return True
        if isinstance(item, str):
            return item in str(self.get("message", ""))
        return False


class StudyCommandParseResult(tuple):
    """Tuple `(is_study_command, remainder)` with named attribute access."""

    is_study_command: bool
    remainder: str
    is_bare_command: bool

    def __new__(cls, is_study_command: bool, remainder: str):
        instance = super().__new__(cls, (is_study_command, remainder))
        instance.is_study_command = is_study_command
        instance.remainder = remainder
        instance.is_bare_command = bool(is_study_command and not remainder.strip())
        return instance


@dataclass
class StudyOrchestrationContext:
    """
    Server-verified Study Mode context injected into PwanimateOrchestrator.
    """

    session_id: str
    learning_objective: str = ""
    current_topic: str = ""
    latest_checkpoint: Optional[Dict[str, Any]] = None
    relevant_older_messages: List[Dict[str, Any]] = field(default_factory=list)
    unavailable_documents: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def older_relevant_excerpts(self) -> List[Dict[str, Any]]:
        return self.relevant_older_messages

    def to_prompt_block(self) -> str:
        return self.format_prompt_block()

    def format_prompt_block(self) -> str:
        """Format structured Study Mode continuity state within XML data boundaries."""
        lines = [
            f'<study_mode_context session_id="{self.session_id}">',
            "  [STUDY MODE ACTIVE]",
        ]
        if self.learning_objective:
            lines.append(f"  <learning_objective>{self.learning_objective}</learning_objective>")
        if self.current_topic:
            lines.append(f"  <current_topic>{self.current_topic}</current_topic>")

        if self.latest_checkpoint:
            ckpt = self.latest_checkpoint
            lines.append(
                f'  <!-- Latest Learning Checkpoint -->'
            )
            lines.append(
                f'  <latest_checkpoint up_to_message_id="{ckpt.get("up_to_message_id") or ""}">'
            )
            if ckpt.get("concepts_explained"):
                lines.append(
                    f"    <concepts_explained>{json.dumps(ckpt['concepts_explained'])}</concepts_explained>"
                )
            if ckpt.get("concepts_demonstrated"):
                lines.append(
                    f"    <verified_concepts_demonstrated>{json.dumps(ckpt['concepts_demonstrated'])}</verified_concepts_demonstrated>"
                )
            if ckpt.get("inferred_understanding"):
                lines.append(
                    f"    <unverified_inferred_understanding>{ckpt['inferred_understanding']}</unverified_inferred_understanding>"
                )
            if ckpt.get("misconceptions"):
                lines.append(
                    f"    <misconceptions_and_open_questions>{json.dumps(ckpt['misconceptions'])}</misconceptions_and_open_questions>"
                )
            if ckpt.get("key_discoveries"):
                lines.append(
                    f"    <key_discoveries>{json.dumps(ckpt['key_discoveries'])}</key_discoveries>"
                )
            if ckpt.get("recommended_next_step"):
                lines.append(
                    f"    <recommended_next_step>{ckpt['recommended_next_step']}</recommended_next_step>"
                )
            if ckpt.get("document_state"):
                lines.append(
                    f"    <checkpoint_document_state>{json.dumps(ckpt['document_state'])}</checkpoint_document_state>"
                )
            lines.append("  </latest_checkpoint>")

        if self.relevant_older_messages:
            lines.append("  <!-- Relevant Earlier Session Excerpts -->")
            lines.append("  <relevant_earlier_transcript_turns>")
            for item in self.relevant_older_messages:
                lines.append(
                    f'    <turn id="{item.get("id")}" role="{item.get("role")}">{item.get("content")}</turn>'
                )
            lines.append("  </relevant_earlier_transcript_turns>")

        if self.unavailable_documents:
            lines.append("  <unavailable_source_warnings>")
            for doc_warn in self.unavailable_documents:
                lines.append(
                    f'    <warning title="{doc_warn.get("title", "Document")}" '
                    f'page="{doc_warn.get("page_number") or ""}">'
                    f'{doc_warn.get("message", "No extracted text is available for this document.")}'
                    f"</warning>"
                )
            lines.append("  </unavailable_source_warnings>")

        lines.append("</study_mode_context>")
        return "\n".join(lines)


def parse_study_command(raw_message: Optional[str]) -> StudyCommandParseResult:
    """
    Detect a leading `@study` command at the start of the trimmed message.

    Returns:
        StudyCommandParseResult((is_study_command, remaining_query_text))
        - "@study Probability" -> (True, "Probability")
        - "@study" or "  @STUDY  " -> (True, "")
        - "Tell me about @study groups" -> (False, "Tell me about @study groups")
        - "@studybuddy help" -> (False, "@studybuddy help")
    """
    if not raw_message or not isinstance(raw_message, str):
        return StudyCommandParseResult(False, "")
    match = STUDY_COMMAND_REGEX.match(raw_message)
    if not match:
        return StudyCommandParseResult(False, raw_message.strip())
    remainder = (match.group(1) or "").strip()
    return StudyCommandParseResult(True, remainder)


def is_user_authorized_for_document(
    user: Any, document: Optional[Document], require_ready: bool = True
) -> bool:
    """
    Verify whether `user` is currently authorized to read `document` in Pwanimate.
    Enforces publication/ready status, availability, and visibility/enrollment rules.

    `require_ready=False` relaxes ONLY the ready/available gate so callers can
    distinguish "still processing" / "unavailable" from "inaccessible" for
    diagnostics. Visibility, ownership, deletion and AI-generation rules are
    unchanged. Content retrieval must keep the strict default.
    """
    if not document or not getattr(user, "is_authenticated", False):
        return False
    if getattr(document, "is_deleted", False):
        return False
    if require_ready and (document.status != "ready" or not document.is_available):
        return False

    if document.uploaded_by_id == user.id:
        return True

    # Private AI-generated documents are strictly restricted to their uploader
    if getattr(document, "is_ai_generated", False):
        return False

    is_admin_or_leader = (
        user.is_staff
        or user.is_superuser
        or getattr(user, "global_role", None) in {"PRESIDENT", "DELEGATE"}
    )
    if is_admin_or_leader:
        return True

    if document.visibility == "public":
        return True

    if document.visibility == "restricted":
        enrolled_unit_ids = set()
        if hasattr(user, "get_enrolled_units"):
            try:
                enrolled_unit_ids = {
                    e.academic_unit_id for e in user.get_enrolled_units(auto_sync=False)
                }
            except Exception:
                enrolled_unit_ids = set()

        if enrolled_unit_ids and document.academic_units.filter(
            academic_unit_id__in=enrolled_unit_ids
        ).exists():
            return True

        if document.academic_units.filter(
            academic_unit__student_enrollments__user=user,
            academic_unit__student_enrollments__is_active=True,
        ).exists():
            return True

        programme = getattr(user, "programme", None)
        if programme and document.academic_units.filter(
            academic_unit__programme_units__programme=programme
        ).exists():
            return True

        course = getattr(user, "course", None)
        if course and document.academic_units.filter(
            academic_unit__code__icontains=course.name
        ).exists():
            return True

    return False


def inspect_open_document_availability(
    user: Any, context_resources: Optional[List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """
    Inspect `context_resources` for documents that are open in the UI but have
    no extracted text chunks available to the AI ("Open in UI != Available to AI").
    """
    warnings: List[Dict[str, Any]] = []
    if not context_resources or not isinstance(context_resources, list):
        return warnings

    seen_doc_keys = set()
    for res in context_resources:
        if not isinstance(res, dict):
            continue
        res_type = str(
            res.get("type") or res.get("resourceType") or res.get("category") or ""
        ).lower()
        source_type = str(res.get("source_type") or res.get("sourceType") or "").lower()
        if source_type == "attachment" or res_type == "attachment":
            att_id = res.get("attachmentId") or res.get("attachment_id") or res.get("id")
            if att_id:
                att = AttachmentService.get_authorized_attachment(user, att_id)
                if att and att.attachment_type == "document":
                    if not att.chunks.exists():
                        warnings.append(StudyWarningDict({
                            "type": "attachment",
                            "id": str(att.id),
                            "title": att.file_name,
                            "page_number": res.get("pageNumber") or res.get("page_number") or res.get("page"),
                            "reason": "no_extracted_text",
                            "message": (
                                f'"{att.file_name}" is open in the viewer, but has no indexed or extractable text '
                                "available. Pwanimate cannot read its pages directly."
                            ),
                        }))
            continue

        if res_type not in {"document", "doc", ""} and source_type not in {"document", "doc", ""}:
            continue

        doc_id = res.get("document_id") or res.get("documentId")
        share_id = (
            res.get("share_id")
            or res.get("document_share_id")
            or res.get("documentShareId")
        )
        resource_id = res.get("id")
        if not doc_id and not share_id and resource_id:
            try:
                doc_id = int(resource_id)
            except (ValueError, TypeError):
                share_id = resource_id

        doc_obj = None
        if share_id:
            try:
                doc_obj = Document.objects.filter(share_id=uuid.UUID(str(share_id).strip())).first()
            except (ValueError, TypeError):
                doc_obj = None
        if not doc_obj and doc_id:
            try:
                doc_obj = Document.objects.filter(id=int(doc_id)).first()
            except (ValueError, TypeError):
                doc_obj = None

        if not doc_obj or not is_user_authorized_for_document(user, doc_obj):
            continue

        dedup_key = str(doc_obj.share_id or doc_obj.id)
        if dedup_key in seen_doc_keys:
            continue
        seen_doc_keys.add(dedup_key)

        page_raw = res.get("pageNumber") or res.get("page_number") or res.get("page")
        page_num = None
        if page_raw is not None:
            try:
                page_num = max(1, int(page_raw))
            except (ValueError, TypeError):
                page_num = None

        active_chunks_qs = DocumentChunk.objects.filter(document_id=doc_obj.id, is_active=True)
        if not active_chunks_qs.exists():
            warnings.append(StudyWarningDict({
                "type": "document",
                "id": str(doc_obj.share_id),
                "document_id": doc_obj.id,
                "title": doc_obj.title,
                "page_number": page_num,
                "reason": "no_extracted_text",
                "message": (
                    f'"{doc_obj.title}" is open in the viewer, but has no indexed or extractable text chunks available '
                    "(for example, it may be a scanned PDF or pending text extraction). "
                    "Pwanimate cannot read the document text directly."
                ),
            }))
        elif page_num is not None:
            page_has_chunks = active_chunks_qs.filter(
                Q(page_number=page_num)
                | Q(page_number__lte=page_num, page_end__gte=page_num)
            ).exists()
            if not page_has_chunks:
                warnings.append(StudyWarningDict({
                    "type": "document",
                    "id": str(doc_obj.share_id),
                    "document_id": doc_obj.id,
                    "title": doc_obj.title,
                    "page_number": page_num,
                    "reason": "page_text_unavailable",
                    "message": (
                        f'Page {page_num} of "{doc_obj.title}" is open in the viewer, but has no extractable text '
                        f"for page {page_num}."
                    ),
                }))

    return warnings


class RestoredContextResult(tuple):
    """Tuple `(resources, unavailable_notices)` that also supports dict-key subscripting."""

    def __new__(cls, resources: List[Dict[str, Any]], unavailable_notices: List[Dict[str, Any]]):
        instance = super().__new__(cls, (resources, unavailable_notices))
        instance._mapping = {
            "resources": resources,
            "restored_context": resources,
            "unavailable_notices": unavailable_notices,
            "unavailable_resources": unavailable_notices,
        }
        return instance

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, str):
            return self._mapping[key]
        return super().__getitem__(key)


class StudySummaryResult:
    """
    Hybrid result returned by `StudySessionService.generate_session_summary`.
    Supports both tuple unpacking (`session, summary_msg = ...`) and dict unpacking/indexing
    (`payload["summary_markdown"]`, `{**payload}`).
    """

    def __init__(self, session: PwanimateStudySession, message: PwanimateMessage):
        self.session = session
        self.message = message
        title = (
            (session.conversation.title if session.conversation and session.conversation.title else "")
            or session.current_topic
            or "Study Session"
        )
        self._data: Dict[str, Any] = {
            "summary_markdown": message.content or "",
            "message_id": message.id,
            "title": f"Study Summary: {title}",
            "summary": {
                "message_id": message.id,
                "content": message.content or "",
                "title": f"Study Summary: {title}",
                "created_at": message.created_at.isoformat() if message.created_at else None,
            },
        }

    def __iter__(self):
        yield self.session
        yield self.message

    def keys(self):
        return self._data.keys()

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return (self.session, self.message)[key]
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


class StudySessionService:
    """
    Service managing PwanimateStudySession lifecycle, Context Rail persistence,
    learning checkpoints, bounded context reconstruction, summaries, and collections.
    """

    STUDY_HISTORY_MAX_MESSAGES = STUDY_HISTORY_MAX_MESSAGES
    STUDY_HISTORY_MAX_TOKENS = STUDY_HISTORY_MAX_TOKENS
    STUDY_OLDER_RETRIEVAL_MAX_MESSAGES = STUDY_OLDER_RETRIEVAL_MAX_MESSAGES
    STUDY_OLDER_RETRIEVAL_MAX_TOKENS = STUDY_OLDER_RETRIEVAL_MAX_TOKENS
    CHECKPOINT_AUTO_INTERVAL_MESSAGES = CHECKPOINT_AUTO_INTERVAL_MESSAGES

    @staticmethod
    def _resolve_user_and_session_id(
        user_or_session: Any,
        session_id: Optional[Any] = None,
        user: Optional[Any] = None,
    ) -> Tuple[Any, Optional[uuid.UUID]]:
        """Normalize `(user, session_id)` vs `(session, user=...)` calling conventions."""
        if isinstance(user_or_session, PwanimateStudySession):
            resolved_user = user or user_or_session.user
            return resolved_user, user_or_session.id
        resolved_user = user_or_session if user is None else user
        if not session_id:
            return resolved_user, None
        try:
            sess_uuid = session_id if isinstance(session_id, uuid.UUID) else uuid.UUID(str(session_id))
        except (ValueError, TypeError, AttributeError):
            sess_uuid = None
        return resolved_user, sess_uuid

    @staticmethod
    def get_owned_session(user: Any, session_id: Any) -> Optional[PwanimateStudySession]:
        """Fetch a Study Mode session strictly owned by `user`."""
        if isinstance(session_id, PwanimateStudySession):
            session_id = session_id.id
        if not user or not getattr(user, "is_authenticated", False) or not session_id:
            return None
        try:
            sess_uuid = session_id if isinstance(session_id, uuid.UUID) else uuid.UUID(str(session_id))
        except (ValueError, TypeError, AttributeError):
            return None
        return (
            PwanimateStudySession.objects.select_related("conversation", "latest_checkpoint")
            .filter(id=sess_uuid, user=user)
            .first()
        )

    @staticmethod
    def get_session_for_conversation(
        user: Any, conversation: Optional[PwanimateConversation]
    ) -> Optional[PwanimateStudySession]:
        """Return the study session linked to `conversation` if owned by `user`."""
        if not user or not getattr(user, "is_authenticated", False) or not conversation:
            return None
        if conversation.user_id != user.id:
            return None
        return (
            PwanimateStudySession.objects.select_related("conversation", "latest_checkpoint")
            .filter(conversation=conversation, user=user)
            .first()
        )

    @staticmethod
    def get_active_session(user: Any) -> Optional[PwanimateStudySession]:
        """Return the user's single active study session, if one exists."""
        if not user or not getattr(user, "is_authenticated", False):
            return None
        return (
            PwanimateStudySession.objects.select_related("conversation", "latest_checkpoint")
            .filter(user=user, status=PwanimateStudySession.STATUS_ACTIVE)
            .order_by("-last_active_at")
            .first()
        )

    @staticmethod
    def get_resumable_session(user: Any) -> Optional[PwanimateStudySession]:
        """
        Return the most recently active unfinished (active or paused) Study Mode session
        eligible for the `/pwanimate/` resume banner.

        A dismissed banner stays hidden until new activity occurs (`last_active_at > resume_banner_dismissed_at`).
        """
        if not user or not getattr(user, "is_authenticated", False):
            return None
        candidates = (
            PwanimateStudySession.objects.select_related("conversation", "latest_checkpoint")
            .filter(
                user=user,
                status__in=[
                    PwanimateStudySession.STATUS_ACTIVE,
                    PwanimateStudySession.STATUS_PAUSED,
                ],
            )
            .order_by("-last_active_at", "-started_at")[:5]
        )
        for session in candidates:
            if (
                session.resume_banner_dismissed_at is not None
                and session.last_active_at is not None
                and session.resume_banner_dismissed_at >= session.last_active_at
            ):
                continue
            return session
        return None

    @staticmethod
    def list_user_sessions(
        user: Any, status_filter: Optional[str] = None, limit: int = 50
    ) -> List[PwanimateStudySession]:
        """List a user's Study Mode sessions ordered by most recent activity."""
        if not user or not getattr(user, "is_authenticated", False):
            return []
        qs = PwanimateStudySession.objects.select_related(
            "conversation", "latest_checkpoint"
        ).filter(user=user)
        if status_filter in {
            PwanimateStudySession.STATUS_ACTIVE,
            PwanimateStudySession.STATUS_PAUSED,
            PwanimateStudySession.STATUS_COMPLETED,
        }:
            qs = qs.filter(status=status_filter)
        return list(qs.order_by("-last_active_at", "-started_at")[: max(1, min(limit, 100))])

    @classmethod
    def _pause_other_active_sessions(cls, user: Any, exclude_session_id: Optional[uuid.UUID] = None,
                                     unit_scope: str = "general") -> int:
        """
        Demote this user's other active sessions for the SAME unit scope to `paused`.
        Sessions on different units stay active — a student can study several
        units in parallel. Caller must run inside `transaction.atomic()`.
        """
        qs = PwanimateStudySession.objects.select_for_update().filter(
            user=user,
            status=PwanimateStudySession.STATUS_ACTIVE,
            unit_scope=unit_scope,
        )
        if exclude_session_id:
            qs = qs.exclude(id=exclude_session_id)
        now = timezone.now()
        return qs.update(status=PwanimateStudySession.STATUS_PAUSED, updated_at=now)

    @classmethod
    def start_or_continue_session(
        cls,
        user: Any,
        conversation: Optional[PwanimateConversation] = None,
        query: str = "",
        title: str = "",
        context_resources: Optional[List[Dict[str, Any]]] = None,
        attachments: Optional[List[Any]] = None,
        learning_objective: str = "",
        current_topic: str = "",
        unit: Any = None,
        unit_label: str = "",
        target_goal: str = "",
        assessment_mode: str = "",
    ) -> PwanimateStudySession:
        """
        Start a new active Study Mode session or continue/activate the session for `conversation`.

        Enforces the per-unit active invariant transactionally: starting or activating
        a session pauses other active sessions for the SAME unit only, so a student
        can run parallel sessions across different units.
        """
        if not user or not getattr(user, "is_authenticated", False):
            raise ValueError("Authenticated user is required for Study Mode.")

        clean_query = (query or "").strip()
        derived_title = (
            (title or "").strip()[:255]
            or (ConversationService.derive_title(clean_query) if clean_query else "")
        )
        derived_topic = (current_topic or derived_title or clean_query[:120]).strip()[:255]
        derived_objective = (learning_objective or clean_query).strip()

        from pwanimate.services.personal_planner import PersonalPlannerService
        unit_obj, resolved_label = PersonalPlannerService.resolve_unit(unit, unit_label)
        unit_scope = PersonalPlannerService.unit_scope_for(unit_obj, resolved_label or unit_label)
        clean_mode = (assessment_mode or '').strip().lower()
        if clean_mode not in ('', 'immediate', 'deferred', 'mcq'):
            raise ValueError("assessment_mode must be 'immediate', 'deferred', or 'mcq'.")
        clean_goal = (target_goal or '').strip()

        merged_resources: Optional[List[Dict[str, Any]]] = (
            list(context_resources) if isinstance(context_resources, list) else None
        )
        if attachments:
            if merged_resources is None:
                merged_resources = []
            for att in attachments:
                att_id = getattr(att, "id", None)
                if att_id:
                    merged_resources.append({
                        "type": "attachment",
                        "attachmentId": str(att_id),
                        "title": getattr(att, "file_name", "Attachment"),
                    })

        now = timezone.now()
        with transaction.atomic():
            if conversation is not None:
                if conversation.user_id != user.id:
                    raise PermissionError("Cannot attach Study Mode session to another user's conversation.")
                existing = (
                    PwanimateStudySession.objects.select_for_update()
                    .filter(conversation=conversation, user=user)
                    .first()
                )
                if existing:
                    # Without explicit unit params the session keeps its own
                    # scope — pause peers of THAT scope, not 'general'.
                    pause_scope = (unit_scope if (unit_obj is not None or unit_label)
                                   else (existing.unit_scope or unit_scope))
                    cls._pause_other_active_sessions(user, exclude_session_id=existing.id, unit_scope=pause_scope)
                    existing.status = PwanimateStudySession.STATUS_ACTIVE
                    existing.ended_at = None
                    existing.last_active_at = now
                    existing.resume_banner_dismissed_at = None
                    if unit_obj is not None or unit_label:
                        existing.unit = unit_obj
                        existing.unit_label = resolved_label
                        existing.unit_scope = unit_scope
                    if learning_objective:
                        existing.learning_objective = learning_objective.strip()
                    elif not existing.learning_objective and derived_objective:
                        existing.learning_objective = derived_objective
                    if current_topic:
                        existing.current_topic = current_topic.strip()[:255]
                    elif derived_topic and not existing.current_topic:
                        existing.current_topic = derived_topic
                    if clean_goal:
                        existing.target_goal = clean_goal
                        existing.target_set_at = now
                        existing.progress_percent = 0
                    if clean_mode:
                        existing.assessment_mode = clean_mode
                    if derived_title and not conversation.title:
                        conversation.title = derived_title
                        conversation.save(update_fields=["title", "updated_at"])
                    existing.save(
                        update_fields=[
                            "status",
                            "ended_at",
                            "last_active_at",
                            "resume_banner_dismissed_at",
                            "learning_objective",
                            "current_topic",
                            "unit",
                            "unit_label",
                            "unit_scope",
                            "target_goal",
                            "target_set_at",
                            "progress_percent",
                            "assessment_mode",
                            "updated_at",
                        ]
                    )
                    session = existing
                else:
                    cls._pause_other_active_sessions(user, unit_scope=unit_scope)
                    if not conversation.title and derived_title:
                        conversation.title = derived_title
                        conversation.save(update_fields=["title", "updated_at"])
                    session = PwanimateStudySession.objects.create(
                        user=user,
                        conversation=conversation,
                        status=PwanimateStudySession.STATUS_ACTIVE,
                        learning_objective=derived_objective,
                        current_topic=derived_topic,
                        unit=unit_obj,
                        unit_label=resolved_label,
                        unit_scope=unit_scope,
                        target_goal=clean_goal,
                        target_set_at=now if clean_goal else None,
                        assessment_mode=clean_mode,
                        last_active_at=now,
                    )
            else:
                cls._pause_other_active_sessions(user, unit_scope=unit_scope)
                conversation = ConversationService.create_conversation(
                    user=user,
                    title=derived_title,
                )
                session = PwanimateStudySession.objects.create(
                    user=user,
                    conversation=conversation,
                    status=PwanimateStudySession.STATUS_ACTIVE,
                    learning_objective=derived_objective,
                    current_topic=derived_topic,
                    unit=unit_obj,
                    unit_label=resolved_label,
                    unit_scope=unit_scope,
                    target_goal=clean_goal,
                    target_set_at=now if clean_goal else None,
                    assessment_mode=clean_mode,
                    last_active_at=now,
                )

            if merged_resources is not None:
                cls.sync_context_state(session, user, merged_resources)

        return cls.get_owned_session(user, session.id) or session

    @classmethod
    def get_or_create_unit_session(cls, user: Any, unit: Any = None, unit_label: str = "",
                                   title: str = "", learning_objective: str = "",
                                   target_goal: str = "", assessment_mode: str = "") -> PwanimateStudySession:
        """
        Find or start the dedicated study session for one unit scope.

        Each unit gets its own conversation + session so parallel units never
        collide. A paused session for the same scope is reactivated and resumes
        from its latest checkpoint; completed sessions start fresh.
        """
        from pwanimate.services.personal_planner import PersonalPlannerService
        unit_obj, resolved_label = PersonalPlannerService.resolve_unit(unit, unit_label)
        scope = PersonalPlannerService.unit_scope_for(unit_obj, resolved_label)
        existing = (
            PwanimateStudySession.objects.select_related("conversation")
            .filter(user=user, unit_scope=scope,
                    status__in=[PwanimateStudySession.STATUS_ACTIVE, PwanimateStudySession.STATUS_PAUSED])
            .order_by("-last_active_at")
            .first()
        )
        if existing is not None:
            return cls.start_or_continue_session(
                user=user, conversation=existing.conversation, unit=unit_obj,
                unit_label=resolved_label, title=title, learning_objective=learning_objective,
                target_goal=target_goal, assessment_mode=assessment_mode)
        label = resolved_label or (getattr(unit_obj, 'name', '') or 'Study')
        conversation = ConversationService.create_conversation(
            user=user, title=(title or f"Study — {label}")[:255])
        return cls.start_or_continue_session(
            user=user, conversation=conversation, unit=unit_obj, unit_label=resolved_label,
            title=title, learning_objective=learning_objective,
            target_goal=target_goal, assessment_mode=assessment_mode)

    ROADMAP_STATUSES = ("pending", "doing", "done")
    MAX_ROADMAP_ITEMS = 12

    @classmethod
    def normalize_roadmap(cls, items: Any) -> List[Dict[str, str]]:
        """Validate a roadmap payload into [{label, status}] rows."""
        if not isinstance(items, list) or not items:
            raise ValueError("A roadmap needs at least one section.")
        if len(items) > cls.MAX_ROADMAP_ITEMS:
            raise ValueError(f"A roadmap supports at most {cls.MAX_ROADMAP_ITEMS} sections.")
        clean = []
        for item in items:
            if not isinstance(item, dict) or not str(item.get("label") or "").strip():
                raise ValueError("Every roadmap section needs a label.")
            status = str(item.get("status") or "pending").strip().lower()
            if status not in cls.ROADMAP_STATUSES:
                raise ValueError("Roadmap status must be pending, doing, or done.")
            clean.append({"label": str(item["label"]).strip()[:200], "status": status})
        return clean

    @classmethod
    def roadmap_coverage(cls, session: PwanimateStudySession) -> int:
        """Percent of roadmap sections marked done; 100 when no roadmap exists."""
        roadmap = session.roadmap if isinstance(session.roadmap, list) else []
        if not roadmap:
            return 100
        done = sum(1 for item in roadmap if isinstance(item, dict) and item.get("status") == "done")
        return round(done / len(roadmap) * 100)

    @classmethod
    def recompute_progress(cls, session: PwanimateStudySession, save: bool = True) -> int:
        """
        progress_percent = mean(coverage, mastery) when a roadmap exists, else mastery.
        Finishing every section without passing tests caps progress at ~50% —
        tests are the gate to 100%, and self-reported understanding moves nothing.
        """
        mastery = max(0, min(100, int(session.mastery_percent or 0)))
        roadmap = session.roadmap if isinstance(session.roadmap, list) else []
        if roadmap:
            progress = round((cls.roadmap_coverage(session) + mastery) / 2)
        else:
            progress = mastery
        progress = max(0, min(100, progress))
        if session.progress_percent != progress:
            session.progress_percent = progress
            if save:
                session.save(update_fields=["progress_percent", "updated_at"])
        return progress

    @classmethod
    def set_roadmap(cls, session: PwanimateStudySession, items: Any) -> PwanimateStudySession:
        session.roadmap = cls.normalize_roadmap(items)
        session.save(update_fields=["roadmap", "updated_at"])
        cls.recompute_progress(session)
        return session

    @classmethod
    def update_roadmap_item(cls, session: PwanimateStudySession, index: int,
                            status: Optional[str] = None) -> PwanimateStudySession:
        """Move one roadmap section; completing it advances coverage, never mastery."""
        roadmap = session.roadmap if isinstance(session.roadmap, list) else []
        if not isinstance(index, int) or not 0 <= index < len(roadmap):
            raise ValueError("Roadmap section index out of range.")
        if status is not None:
            status = str(status).strip().lower()
            if status not in cls.ROADMAP_STATUSES:
                raise ValueError("Roadmap status must be pending, doing, or done.")
            roadmap[index]["status"] = status
        session.roadmap = roadmap
        session.save(update_fields=["roadmap", "updated_at"])
        cls.recompute_progress(session)
        return session

    @classmethod
    def set_target(cls, session: PwanimateStudySession, goal: str, unit: Any = None,
                   unit_label: str = "", assessment_mode: str = "") -> PwanimateStudySession:
        """Set or replace the session's end goal; a new target restarts assessed progress."""
        from pwanimate.services.personal_planner import PersonalPlannerService
        clean_goal = (goal or "").strip()
        if not clean_goal:
            raise ValueError("A study target needs a goal.")
        clean_mode = (assessment_mode or "").strip().lower()
        if clean_mode not in ("", "immediate", "deferred", "mcq"):
            raise ValueError("assessment_mode must be 'immediate', 'deferred', or 'mcq'.")
        unit_obj, resolved_label = PersonalPlannerService.resolve_unit(unit, unit_label or session.unit_label)
        session.target_goal = clean_goal
        session.target_set_at = timezone.now()
        session.progress_percent = 0
        session.mastery_percent = 0
        session.roadmap = []
        if clean_mode:
            session.assessment_mode = clean_mode
        scope_changed = False
        if unit_obj is not None or unit_label:
            session.unit = unit_obj
            session.unit_label = resolved_label
            session.unit_scope = PersonalPlannerService.unit_scope_for(unit_obj, resolved_label)
            scope_changed = True
        fields = ["target_goal", "target_set_at", "progress_percent",
                  "mastery_percent", "roadmap",
                  "assessment_mode", "unit", "unit_label", "updated_at"]
        if scope_changed:
            fields.append("unit_scope")
        session.save(update_fields=fields)
        return session

    @classmethod
    def pause_session(
        cls,
        user_or_session: Any,
        session_id: Optional[Any] = None,
        user: Optional[Any] = None,
    ) -> Optional[PwanimateStudySession]:
        """Pause an active study session without deleting any history."""
        resolved_user, resolved_id = cls._resolve_user_and_session_id(
            user_or_session, session_id=session_id, user=user
        )
        if not resolved_user or not resolved_id:
            return None
        with transaction.atomic():
            session = (
                PwanimateStudySession.objects.select_for_update()
                .select_related("conversation")
                .filter(id=resolved_id, user=resolved_user)
                .first()
            )
            if not session:
                return None
            if session.status == PwanimateStudySession.STATUS_ACTIVE:
                session.status = PwanimateStudySession.STATUS_PAUSED
                session.save(update_fields=["status", "updated_at"])
        return cls.get_owned_session(resolved_user, resolved_id)

    @classmethod
    def resume_session(
        cls,
        user_or_session: Any,
        session_id: Optional[Any] = None,
        user: Optional[Any] = None,
    ) -> Optional[PwanimateStudySession]:
        """
        Resume a paused (or completed/active) study session, pausing any other active session
        for the same user according to the single-active-session policy.
        """
        resolved_user, resolved_id = cls._resolve_user_and_session_id(
            user_or_session, session_id=session_id, user=user
        )
        if not resolved_user or not resolved_id:
            return None
        with transaction.atomic():
            session = (
                PwanimateStudySession.objects.select_for_update()
                .select_related("conversation")
                .filter(id=resolved_id, user=resolved_user)
                .first()
            )
            if not session:
                return None
            cls._pause_other_active_sessions(resolved_user, exclude_session_id=session.id)
            now = timezone.now()
            session.status = PwanimateStudySession.STATUS_ACTIVE
            session.ended_at = None
            session.last_active_at = now
            session.resume_banner_dismissed_at = None
            session.save(
                update_fields=[
                    "status",
                    "ended_at",
                    "last_active_at",
                    "resume_banner_dismissed_at",
                    "updated_at",
                ]
            )
        return cls.get_owned_session(resolved_user, resolved_id)

    @classmethod
    def end_session(
        cls,
        user_or_session: Any,
        session_id: Optional[Any] = None,
        user: Optional[Any] = None,
        generate_final_checkpoint: bool = False,
    ) -> Optional[PwanimateStudySession]:
        """
        Explicitly end/complete a Study Mode session while preserving its conversation,
        messages, attachments, and checkpoints.
        """
        resolved_user, resolved_id = cls._resolve_user_and_session_id(
            user_or_session, session_id=session_id, user=user
        )
        if not resolved_user or not resolved_id:
            return None
        with transaction.atomic():
            session = (
                PwanimateStudySession.objects.select_for_update()
                .select_related("conversation")
                .filter(id=resolved_id, user=resolved_user)
                .first()
            )
            if not session:
                return None
            now = timezone.now()
            session.status = PwanimateStudySession.STATUS_COMPLETED
            session.ended_at = now
            session.save(update_fields=["status", "ended_at", "updated_at"])

        if generate_final_checkpoint:
            try:
                cls.generate_checkpoint(session_id=resolved_id)
            except Exception as exc:
                logger.debug("Optional final checkpoint on session end skipped: %s", exc)

        return cls.get_owned_session(resolved_user, resolved_id)

    @classmethod
    def dismiss_resume_banner(
        cls,
        user_or_session: Any,
        session_id: Optional[Any] = None,
        user: Optional[Any] = None,
    ) -> Optional[PwanimateStudySession]:
        """
        Dismiss the resume banner for a session without ending or deleting the session.
        New activity (`last_active_at > resume_banner_dismissed_at`) will make it eligible again.
        """
        if isinstance(user_or_session, PwanimateStudySession):
            resolved_user = user or user_or_session.user
            resolved_id = user_or_session.id
        else:
            resolved_user = user_or_session if user is None else user
            resolved_id = session_id

        if not resolved_user:
            return None

        with transaction.atomic():
            if resolved_id:
                session = (
                    PwanimateStudySession.objects.select_for_update()
                    .select_related("conversation")
                    .filter(id=resolved_id, user=resolved_user)
                    .first()
                )
            else:
                target = cls.get_resumable_session(resolved_user)
                if not target:
                    return None
                session = (
                    PwanimateStudySession.objects.select_for_update()
                    .select_related("conversation")
                    .filter(id=target.id, user=resolved_user)
                    .first()
                )
            if not session:
                return None
            session.resume_banner_dismissed_at = timezone.now()
            session.save(update_fields=["resume_banner_dismissed_at", "updated_at"])
            target_id = session.id
        return cls.get_owned_session(resolved_user, target_id)

    @classmethod
    def update_session_metadata(
        cls,
        user: Optional[Any] = None,
        session_id: Optional[Any] = None,
        session: Optional[PwanimateStudySession] = None,
        title: Optional[str] = None,
        learning_objective: Optional[str] = None,
        current_topic: Optional[str] = None,
        context_resources: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[PwanimateStudySession]:
        """Update session title, learning objective, current topic, or Context Rail state."""
        resolved_user, resolved_id = cls._resolve_user_and_session_id(
            session if session is not None else user,
            session_id=session_id,
            user=user,
        )
        if not resolved_user or not resolved_id:
            return None
        with transaction.atomic():
            locked = (
                PwanimateStudySession.objects.select_for_update()
                .select_related("conversation")
                .filter(id=resolved_id, user=resolved_user)
                .first()
            )
            if not locked:
                return None
            update_fields = ["updated_at"]
            if learning_objective is not None:
                locked.learning_objective = str(learning_objective).strip()[:2000]
                update_fields.append("learning_objective")
            if current_topic is not None:
                locked.current_topic = str(current_topic).strip()[:255]
                update_fields.append("current_topic")
            if title is not None and locked.conversation:
                clean_title = str(title).strip()[:255]
                if clean_title:
                    locked.conversation.title = clean_title
                    locked.conversation.save(update_fields=["title", "updated_at"])
            locked.save(update_fields=update_fields)
            if context_resources is not None:
                cls.sync_context_state(locked, resolved_user, context_resources)
        return cls.get_owned_session(resolved_user, resolved_id)

    @classmethod
    def sync_context_state(
        cls,
        session: PwanimateStudySession,
        user: Any,
        context_resources: Optional[List[Dict[str, Any]]],
        active_index: int = 0,
    ) -> Dict[str, Any]:
        """
        Validate and persist Context Rail resources (documents, attachments, page positions)
        onto `session.context_state`. Only authorized resources are stored.
        """
        if context_resources is None or not isinstance(context_resources, list):
            return session.context_state or {}

        normalized_resources: List[Dict[str, Any]] = []
        active_document: Optional[Dict[str, Any]] = None
        seen_keys = set()

        for res in context_resources:
            if not isinstance(res, dict):
                continue
            res_type = str(
                res.get("type")
                or res.get("previewType")
                or res.get("resourceType")
                or res.get("category")
                or ""
            ).lower()
            source_type = str(res.get("source_type") or res.get("sourceType") or "").lower()
            page_raw = res.get("pageNumber") or res.get("page_number") or res.get("page")
            try:
                page_number = max(1, int(page_raw)) if page_raw is not None else 1
            except (ValueError, TypeError):
                page_number = 1

            # 1. Chat attachment
            if source_type == "attachment" or res_type == "attachment" or res.get("attachmentId") or res.get("attachment_id"):
                att_id = res.get("attachmentId") or res.get("attachment_id") or res.get("id")
                att = AttachmentService.get_authorized_attachment(user, att_id)
                if not att:
                    continue
                key = f"attachment:{att.id}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                entry = {
                    "type": "attachment",
                    "previewType": "image" if att.attachment_type == "image" else "document",
                    "attachment_id": str(att.id),
                    "attachmentId": str(att.id),
                    "title": att.file_name,
                    "attachment_type": att.attachment_type,
                    "file_type": att.extension.lstrip("."),
                    "page_number": page_number,
                    "pageNumber": page_number,
                }
                normalized_resources.append(entry)
                if active_document is None and att.attachment_type == "document":
                    active_document = entry
                continue

            # 2. Repository document
            if res_type in {"document", "doc", ""} or source_type in {"document", "doc", ""}:
                doc_id = res.get("document_id") or res.get("documentId")
                share_id = (
                    res.get("share_id")
                    or res.get("document_share_id")
                    or res.get("documentShareId")
                )
                resource_id = res.get("id")
                if not doc_id and not share_id and resource_id:
                    try:
                        doc_id = int(resource_id)
                    except (ValueError, TypeError):
                        share_id = resource_id

                doc_obj = None
                if share_id:
                    try:
                        doc_obj = Document.objects.filter(
                            share_id=uuid.UUID(str(share_id).strip())
                        ).first()
                    except (ValueError, TypeError):
                        doc_obj = None
                if not doc_obj and doc_id is not None:
                    try:
                        doc_obj = Document.objects.filter(id=int(doc_id)).first()
                    except (ValueError, TypeError):
                        try:
                            doc_obj = Document.objects.filter(
                                share_id=uuid.UUID(str(doc_id).strip())
                            ).first()
                        except (ValueError, TypeError):
                            doc_obj = None

                if not doc_obj or not is_user_authorized_for_document(user, doc_obj):
                    continue

                key = f"document:{doc_obj.share_id}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                entry = {
                    "type": "document",
                    "previewType": "document",
                    "document_id": doc_obj.id,
                    "documentId": str(doc_obj.id),
                    "share_id": str(doc_obj.share_id),
                    "documentShareId": str(doc_obj.share_id),
                    "title": doc_obj.title,
                    "page_number": page_number,
                    "pageNumber": page_number,
                }
                normalized_resources.append(entry)

        if normalized_resources:
            if 0 <= active_index < len(normalized_resources):
                active_document = normalized_resources[active_index]
            elif active_document is None:
                active_document = normalized_resources[0]

        if normalized_resources or not session.context_state:
            state = {
                "resources": normalized_resources,
                "active_document": active_document,
                "active_index": active_index if normalized_resources else 0,
                "updated_at": timezone.now().isoformat(),
            }
            session.context_state = state
            session.save(update_fields=["context_state", "updated_at"])
        return session.context_state or {}

    @classmethod
    def restore_context_state(
        cls, session: PwanimateStudySession, user: Any
    ) -> RestoredContextResult:
        """
        Revalidate and restore saved Context Rail resources on session resume.

        Returns:
            RestoredContextResult((restored_context_resources, unavailable_resource_notices))
        """
        state = session.context_state if isinstance(session.context_state, dict) else {}
        saved_resources = state.get("resources") or []
        if not isinstance(saved_resources, list):
            return RestoredContextResult([], [])

        restored: List[Dict[str, Any]] = []
        notices: List[Dict[str, Any]] = []

        for item in saved_resources:
            if not isinstance(item, dict):
                continue
            item_type = str(
                item.get("type") or item.get("previewType") or item.get("resourceType") or "document"
            ).lower()
            saved_title = str(item.get("title") or "Study Resource")
            page_raw = item.get("page_number") or item.get("pageNumber") or item.get("page") or 1
            try:
                page_number = max(1, int(page_raw))
            except (ValueError, TypeError):
                page_number = 1

            if item_type == "attachment" or item.get("attachment_id") or item.get("attachmentId"):
                att_id = item.get("attachment_id") or item.get("attachmentId")
                att = AttachmentService.get_authorized_attachment(user, att_id)
                if not att or not att.file or att.processing_status == "failed":
                    notices.append(StudyWarningDict({
                        "type": "attachment",
                        "title": saved_title,
                        "reason": "unavailable",
                        "message": f'Previously attached file "{saved_title}" is no longer available.',
                    }))
                    continue
                has_text = True
                if att.attachment_type == "document" and not att.chunks.exists():
                    has_text = False
                restored.append({
                    "id": f"attachment:{att.id}",
                    "resourceType": "image" if att.attachment_type == "image" else "document",
                    "previewType": "image" if att.attachment_type == "image" else "document",
                    "sourceType": "attachment",
                    "attachmentId": str(att.id),
                    "fileType": att.extension.lstrip("."),
                    "mediaUrl": att.url,
                    "url": att.url,
                    "title": att.file_name,
                    "category": "Image" if att.attachment_type == "image" else "Document",
                    "citation": "Attached in this chat",
                    "pageNumber": page_number,
                    "page_number": page_number,
                    "hasExtractedText": has_text,
                })
                continue

            if item_type in {"document", "doc"}:
                share_id = item.get("share_id") or item.get("documentShareId")
                doc_id = item.get("document_id") or item.get("documentId")
                doc_obj = None
                if share_id:
                    try:
                        doc_obj = (
                            Document.objects.select_related("category", "uploaded_by")
                            .filter(share_id=uuid.UUID(str(share_id)))
                            .first()
                        )
                    except (ValueError, TypeError):
                        doc_obj = None
                if not doc_obj and doc_id is not None:
                    try:
                        doc_obj = (
                            Document.objects.select_related("category", "uploaded_by")
                            .filter(id=int(doc_id))
                            .first()
                        )
                    except (ValueError, TypeError):
                        try:
                            doc_obj = (
                                Document.objects.select_related("category", "uploaded_by")
                                .filter(share_id=uuid.UUID(str(doc_id)))
                                .first()
                            )
                        except (ValueError, TypeError):
                            doc_obj = None

                if not doc_obj or not is_user_authorized_for_document(user, doc_obj):
                    notices.append(StudyWarningDict({
                        "type": "document",
                        "title": saved_title,
                        "reason": "unavailable",
                        "message": (
                            f'Previously used document "{saved_title}" is no longer available or accessible.'
                        ),
                    }))
                    continue

                latest_version = doc_obj.latest_version
                first_file = latest_version.files.first() if latest_version else None
                media_url = ""
                if first_file and first_file.file:
                    try:
                        media_url = first_file.file.url
                    except (ValueError, OSError):
                        media_url = ""
                if not media_url:
                    media_url = reverse("documents:document_detail", args=[doc_obj.share_id])

                doc_unit_link = doc_obj.academic_units.select_related("academic_unit").first()
                unit_code = (
                    doc_unit_link.academic_unit.code
                    if doc_unit_link and doc_unit_link.academic_unit_id
                    else ""
                )
                unit_name = (
                    doc_unit_link.academic_unit.name
                    if doc_unit_link and doc_unit_link.academic_unit_id
                    else ""
                )
                category_name = doc_obj.category.name if doc_obj.category_id else "Document"
                citation_label = (
                    f"{unit_code} — {unit_name}"
                    if unit_code and unit_name
                    else (unit_code or category_name)
                )

                has_chunks = DocumentChunk.objects.filter(
                    document_id=doc_obj.id, is_active=True
                ).exists()

                restored.append({
                    "resourceType": "document",
                    "previewType": "document",
                    "sourceType": "document",
                    "document_id": doc_obj.id,
                    "documentId": str(doc_obj.share_id),
                    "documentShareId": str(doc_obj.share_id),
                    "documentVersionId": str(latest_version.id) if latest_version else "",
                    "fileId": str(first_file.id) if first_file else "",
                    "fileType": ((first_file.extension or "").lstrip(".") if first_file else "pdf") or "pdf",
                    "mediaUrl": media_url,
                    "thumbnailUrl": (first_file.preview_url or "") if first_file else "",
                    "title": doc_obj.title,
                    "category": category_name,
                    "citation": citation_label,
                    "unitCode": unit_code,
                    "unitName": unit_name,
                    "url": reverse("documents:document_detail", args=[doc_obj.share_id]),
                    "pageNumber": page_number,
                    "page_number": page_number,
                    "hasExtractedText": has_chunks,
                })

        return RestoredContextResult(restored, notices)

    @classmethod
    def serialize_checkpoint(cls, ckpt: Optional[PwanimateStudyCheckpoint]) -> Optional[Dict[str, Any]]:
        """Serialize a PwanimateStudyCheckpoint into a clean dictionary."""
        if not ckpt:
            return None
        return {
            "id": str(ckpt.id),
            "session_id": str(ckpt.session_id),
            "up_to_message_id": ckpt.up_to_message_id,
            "learning_objective": ckpt.learning_objective,
            "current_topic": ckpt.current_topic,
            "concepts_explained": list(ckpt.concepts_explained or []),
            "concepts_demonstrated": list(ckpt.concepts_demonstrated or []),
            "inferred_understanding": ckpt.inferred_understanding or "",
            "misconceptions": list(ckpt.misconceptions or []),
            "key_discoveries": list(ckpt.key_discoveries or []),
            "recommended_next_step": ckpt.recommended_next_step or "",
            "document_state": dict(ckpt.document_state or {}),
            "created_at": ckpt.created_at.isoformat() if ckpt.created_at else None,
        }

    @classmethod
    def serialize_session(
        cls,
        session: Optional[PwanimateStudySession],
        include_restored_context: bool = False,
        user: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        """Serialize a PwanimateStudySession for API responses and template JSON scripts."""
        if not session:
            return None
        conv = session.conversation
        active_doc = None
        if isinstance(session.context_state, dict):
            active_doc = session.context_state.get("active_document")
            if not active_doc:
                resources = session.context_state.get("resources") or []
                if resources and isinstance(resources[0], dict):
                    active_doc = resources[0]

        payload: Dict[str, Any] = {
            "id": str(session.id),
            "conversation_id": str(session.conversation_id),
            "title": (conv.title if conv and conv.title else "") or session.current_topic or "Study Session",
            "status": session.status,
            "learning_objective": session.learning_objective or "",
            "current_topic": session.current_topic or "",
            "unit": str(session.unit_id) if session.unit_id else None,
            "unit_code": getattr(session.unit, "code", "") if session.unit_id else "",
            "unit_label": session.unit_label or "",
            "unit_scope": session.unit_scope or "general",
            "target_goal": session.target_goal or "",
            "target_set_at": session.target_set_at.isoformat() if session.target_set_at else None,
            "progress_percent": session.progress_percent or 0,
            "mastery_percent": session.mastery_percent or 0,
            "roadmap_coverage_percent": cls.roadmap_coverage(session) if session.roadmap else None,
            "roadmap": session.roadmap or [],
            "assessment_mode": session.assessment_mode or "",
            "active_document": active_doc,
            "context_state": session.context_state or {},
            "latest_checkpoint": cls.serialize_checkpoint(session.latest_checkpoint),
            "resume_banner_dismissed_at": (
                session.resume_banner_dismissed_at.isoformat()
                if session.resume_banner_dismissed_at
                else None
            ),
            "started_at": session.started_at.isoformat() if session.started_at else None,
            "last_active_at": session.last_active_at.isoformat() if session.last_active_at else None,
            "ended_at": session.ended_at.isoformat() if session.ended_at else None,
            "url": reverse("pwanimate:conversation_detail", args=[session.conversation_id]),
        }
        if include_restored_context and user is not None:
            restored_resources, notices = cls.restore_context_state(session, user)
            payload["restored_context_resources"] = restored_resources
            payload["unavailable_resource_notices"] = notices
        return payload

    @classmethod
    def build_bounded_recent_history(
        cls,
        session: PwanimateStudySession,
        max_messages: int = STUDY_HISTORY_MAX_MESSAGES,
        max_tokens: int = STUDY_HISTORY_MAX_TOKENS,
        exclude_message_id: Optional[int] = None,
    ) -> List[ChatMessage]:
        """Load the bounded recent message window for a Study Mode session."""
        return ConversationService.load_history(
            session.conversation,
            max_messages=max_messages,
            max_tokens=max_tokens,
            exclude_message_id=exclude_message_id,
        )

    @classmethod
    def build_orchestration_context(
        cls,
        session: PwanimateStudySession,
        user: Optional[Any] = None,
        current_query: str = "",
        query: str = "",
        recent_history: Optional[List[ChatMessage]] = None,
        context_resources: Optional[List[Dict[str, Any]]] = None,
    ) -> StudyOrchestrationContext:
        """Alias for `build_study_orchestration_context`."""
        return cls.build_study_orchestration_context(
            session=session,
            user=user,
            query=current_query or query,
            recent_history=recent_history,
            context_resources=context_resources,
        )

    @classmethod
    def build_study_orchestration_context(
        cls,
        session: PwanimateStudySession,
        user: Optional[Any] = None,
        query: str = "",
        recent_history: Optional[List[ChatMessage]] = None,
        context_resources: Optional[List[Dict[str, Any]]] = None,
    ) -> StudyOrchestrationContext:
        """
        Assemble the bounded Study Mode context for PwanimateOrchestrator:
        1. Latest valid checkpoint (`session.latest_checkpoint`).
        2. Selective retrieval of relevant older conversation turns outside the recent window.
        3. Explicit warnings for any open documents lacking extracted text chunks.
        """
        resolved_user = user or session.user
        if recent_history is None:
            recent_history = cls.build_bounded_recent_history(session)
        unavailable_warnings = inspect_open_document_availability(resolved_user, context_resources)

        # Selective retrieval of older conversation turns if the conversation exceeds the recent window
        older_turns: List[Dict[str, Any]] = []
        conv = session.conversation
        all_msgs_qs = conv.messages.filter(role__in=["user", "assistant"]).order_by("-created_at", "-id")
        recent_count = len(recent_history)
        older_candidates = list(all_msgs_qs[recent_count : recent_count + 40])
        if older_candidates and query:
            keywords = [
                w.lower()
                for w in re.findall(r"[a-zA-Z0-9]{4,}", query)
                if w.lower()
                not in {
                    "what", "when", "where", "which", "explain", "about", "study",
                    "page", "this", "that", "from", "with", "have", "does", "mean",
                }
            ]
            if keywords:
                scored: List[Tuple[int, PwanimateMessage]] = []
                for msg in older_candidates:
                    text_lower = (msg.content or "").lower()
                    hits = sum(1 for kw in keywords if kw in text_lower)
                    if hits > 0:
                        scored.append((hits, msg))
                scored.sort(key=lambda item: (item[0], item[1].id), reverse=True)
                budget_tokens = 0
                selected_older: List[PwanimateMessage] = []
                for _hits, msg in scored[:STUDY_OLDER_RETRIEVAL_MAX_MESSAGES]:
                    snippet = (msg.content or "").strip()[:500]
                    est = estimate_tokens(snippet)
                    if budget_tokens + est > STUDY_OLDER_RETRIEVAL_MAX_TOKENS:
                        continue
                    budget_tokens += est
                    selected_older.append(msg)
                selected_older.sort(key=lambda m: m.id)
                older_turns = [
                    {
                        "id": m.id,
                        "role": m.role,
                        "content": (m.content or "").strip()[:500],
                    }
                    for m in selected_older
                ]

        study_ctx = StudyOrchestrationContext(
            session_id=str(session.id),
            learning_objective=session.learning_objective or "",
            current_topic=session.current_topic or "",
            latest_checkpoint=cls.serialize_checkpoint(session.latest_checkpoint),
            relevant_older_messages=older_turns,
            unavailable_documents=unavailable_warnings,
        )
        return study_ctx

    @classmethod
    def should_auto_checkpoint(cls, session: PwanimateStudySession) -> bool:
        """
        Determine whether enough new messages have accumulated since `latest_checkpoint`
        to warrant generating an updated checkpoint.
        """
        if not session or not session.conversation_id:
            return False
        qs = PwanimateMessage.objects.filter(conversation_id=session.conversation_id)
        if session.latest_checkpoint and session.latest_checkpoint.up_to_message_id:
            new_count = qs.filter(id__gt=session.latest_checkpoint.up_to_message_id).count()
        else:
            new_count = qs.count()
        return new_count >= CHECKPOINT_AUTO_INTERVAL_MESSAGES

    @classmethod
    def _sanitize_demonstrated_concepts(
        cls,
        raw_demonstrated: Any,
        user_messages_by_id: Dict[int, PwanimateMessage],
        fallback_inferred: List[str],
    ) -> List[Dict[str, Any]]:
        """
        Enforce the product invariant that a student's passive agreement or acknowledgment
        alone NEVER counts as demonstrated mastery, and every demonstrated concept must
        reference a genuine substantive user message in the conversation.
        """
        verified: List[Dict[str, Any]] = []
        if not isinstance(raw_demonstrated, list):
            return verified

        # Sort user message IDs chronologically for fallback matching when the LLM omits ID
        substantive_user_msgs = [
            m
            for m in user_messages_by_id.values()
            if (m.content or "").strip()
            and not PASSIVE_ACKNOWLEDGMENT_REGEX.match((m.content or "").strip())
            and len((m.content or "").strip()) >= 12
        ]

        for entry in raw_demonstrated:
            if isinstance(entry, str):
                concept_name = entry.strip()
                msg_id = None
                evidence_text = ""
            elif isinstance(entry, dict):
                concept_name = str(
                    entry.get("concept") or entry.get("topic") or entry.get("name") or ""
                ).strip()
                msg_id = (
                    entry.get("message_id")
                    or entry.get("user_message_id")
                    or entry.get("evidence_message_id")
                )
                evidence_text = str(
                    entry.get("evidence") or entry.get("evidence_summary") or ""
                ).strip()
            else:
                continue

            if not concept_name:
                continue

            matched_msg: Optional[PwanimateMessage] = None
            if msg_id is not None:
                try:
                    matched_msg = user_messages_by_id.get(int(msg_id))
                except (ValueError, TypeError):
                    matched_msg = None

            if matched_msg is not None:
                msg_text = (matched_msg.content or "").strip()
                if PASSIVE_ACKNOWLEDGMENT_REGEX.match(msg_text) or len(msg_text) < 12:
                    fallback_inferred.append(
                        f"Student acknowledged '{concept_name}', but has not yet demonstrated mastery independently."
                    )
                    continue
            else:
                # If no valid user message ID was supplied, check if any substantive user message
                # actually demonstrated it (not a pure question or passive acknowledgment)
                candidate = None
                for u_msg in reversed(substantive_user_msgs):
                    txt = (u_msg.content or "").strip()
                    # A pure short question ("Explain X", "What is X?") is not demonstrated mastery
                    if txt.endswith("?") and len(txt) < 80:
                        continue
                    if re.match(
                        r"^\s*(?:explain|what\s+is|can\s+you|help\s+me|tell\s+me|summarize|quiz\s+me)\b",
                        txt,
                        re.IGNORECASE,
                    ) and len(txt) < 100:
                        continue
                    candidate = u_msg
                    break
                if candidate is None:
                    fallback_inferred.append(
                        f"Concept '{concept_name}' was discussed, awaiting verified student demonstration."
                    )
                    continue
                matched_msg = candidate

            verified.append({
                "concept": concept_name[:255],
                "user_message_id": matched_msg.id,
                "evidence_message_id": str(matched_msg.id),
                "evidence": (evidence_text or (matched_msg.content or "").strip()[:180]),
            })

        return verified

    @classmethod
    def _synthesize_checkpoint_via_llm(
        cls,
        prompt_text: str,
        gateway: Optional[AIGateway] = None,
    ) -> Optional[Any]:
        """Invoke AIGateway to synthesize a structured checkpoint JSON response."""
        ai_gateway = gateway or AIGateway()
        llm_req = LLMRequest(
            task="general",
            messages=[ChatMessage(role="user", content=prompt_text)],
            system_instruction=(
                "You are Pwanimate's learning-state analyzer. Respond ONLY with a valid JSON object."
            ),
            temperature=0.1,
            max_tokens=900,
        )
        llm_resp = ai_gateway.generate(llm_req)
        return (llm_resp.content or "").strip()

    @classmethod
    def generate_checkpoint(
        cls,
        session_id: Optional[Any] = None,
        session: Optional[PwanimateStudySession] = None,
        up_to_message_id: Optional[int] = None,
        gateway: Optional[AIGateway] = None,
        force: bool = False,
    ) -> Optional[PwanimateStudyCheckpoint]:
        """
        Generate and persist a structured `PwanimateStudyCheckpoint` for `session_id` / `session`.

        Protections:
        - Distributed cache lock prevents duplicate concurrent execution.
        - Idempotent on `(session, up_to_message)`.
        - Out-of-order jobs cannot regress `session.latest_checkpoint` to an older message.
        - Provider/AI failures fall back deterministically or preserve `session.latest_checkpoint` without raising.
        """
        target_id = session.id if isinstance(session, PwanimateStudySession) else (
            session_id.id if isinstance(session_id, PwanimateStudySession) else session_id
        )
        if not target_id:
            return None

        session_obj = (
            PwanimateStudySession.objects.select_related("conversation", "latest_checkpoint")
            .filter(id=target_id)
            .first()
        )
        if not session_obj:
            return None

        messages_qs = session_obj.conversation.messages.filter(role__in=["user", "assistant"]).order_by("id")
        if up_to_message_id is not None:
            messages_qs = messages_qs.filter(id__lte=up_to_message_id)
        messages = list(messages_qs)
        if not messages:
            return session_obj.latest_checkpoint

        boundary_msg = messages[-1]

        # Fast-path idempotency: if a checkpoint already exists for this exact boundary message, return it
        existing_for_boundary = PwanimateStudyCheckpoint.objects.filter(
            session=session_obj,
            up_to_message=boundary_msg,
        ).first()
        if existing_for_boundary and not force:
            if (
                session_obj.latest_checkpoint is None
                or (
                    session_obj.latest_checkpoint.up_to_message_id is not None
                    and boundary_msg.id > session_obj.latest_checkpoint.up_to_message_id
                )
            ):
                with transaction.atomic():
                    locked = PwanimateStudySession.objects.select_for_update().get(id=session_obj.id)
                    if (
                        locked.latest_checkpoint_id is None
                        or locked.latest_checkpoint.up_to_message_id is None
                        or boundary_msg.id >= locked.latest_checkpoint.up_to_message_id
                    ):
                        locked.latest_checkpoint = existing_for_boundary
                        locked.save(update_fields=["latest_checkpoint", "updated_at"])
            return existing_for_boundary

        # Prevent out-of-order older job from running at all if a newer checkpoint already exists
        if (
            session_obj.latest_checkpoint
            and session_obj.latest_checkpoint.up_to_message_id is not None
            and boundary_msg.id < session_obj.latest_checkpoint.up_to_message_id
        ):
            return session_obj.latest_checkpoint

        lock_key = f"pwanimate:study_ckpt_lock:{session_obj.id}"
        if not cache.add(lock_key, boundary_msg.id, timeout=CHECKPOINT_LOCK_TIMEOUT_SECONDS):
            logger.debug("Skipping concurrent checkpoint generation for session %s", session_obj.id)
            return session_obj.latest_checkpoint

        try:
            user_messages_by_id = {m.id: m for m in messages if m.role == "user"}
            prev_ckpt = session_obj.latest_checkpoint
            recent_slice = messages[-12:]
            transcript_lines = []
            for m in recent_slice:
                role_label = "STUDENT" if m.role == "user" else "PWANIMATE"
                transcript_lines.append(f"[Message #{m.id} | {role_label}]: {(m.content or '').strip()[:800]}")

            doc_state = {}
            if isinstance(session_obj.context_state, dict):
                active_doc = session_obj.context_state.get("active_document")
                if isinstance(active_doc, dict):
                    doc_state = dict(active_doc)

            prev_summary_json = json.dumps(cls.serialize_checkpoint(prev_ckpt) or {})
            prompt_text = (
                "Analyze the following Study Mode transcript and previous checkpoint to produce an updated "
                "structured learning checkpoint in strict JSON format.\n\n"
                "CRITICAL RULES:\n"
                "1. Separate `concepts_explained` (what Pwanimate taught) from `concepts_demonstrated` (what the student proved).\n"
                "2. An item in `concepts_demonstrated` MUST ONLY be included if a STUDENT message actively demonstrated understanding "
                "(e.g., solved a question, explained a concept in their own words, or applied a formula accurately), and MUST include "
                "`{\"concept\": \"...\", \"message_id\": <int>, \"evidence\": \"...\"}`.\n"
                "3. Passive agreement or acknowledgment from the student (such as 'ok', 'yes', 'makes sense', 'I understand', 'thanks') "
                "MUST NEVER be placed in `concepts_demonstrated`; record tentative observations in `inferred_understanding` instead.\n"
                "4. Return ONLY a valid JSON object with keys: "
                "`learning_objective` (str), `current_topic` (str), `concepts_explained` (list[str]), "
                "`concepts_demonstrated` (list[dict]), `inferred_understanding` (str), "
                "`misconceptions` (list[str]), `key_discoveries` (list[str]), `recommended_next_step` (str).\n\n"
                f"Session Objective: {session_obj.learning_objective or ''}\n"
                f"Current Topic: {session_obj.current_topic or ''}\n"
                f"Active Document State: {json.dumps(doc_state)}\n"
                f"Previous Checkpoint: {prev_summary_json}\n\n"
                "Recent Transcript:\n" + "\n".join(transcript_lines)
            )

            try:
                synth_res = cls._synthesize_checkpoint_via_llm(prompt_text, gateway=gateway)
            except Exception as llm_exc:
                logger.warning(
                    "LLM checkpoint synthesis failed for session %s; using deterministic fallback: %s",
                    session_obj.id,
                    llm_exc,
                )
                synth_res = None

            if isinstance(synth_res, dict):
                parsed = synth_res
            else:
                parsed = cls._parse_checkpoint_json(
                    str(synth_res or ""), session_obj, prev_ckpt, messages
                )

            fallback_inferred: List[str] = []
            raw_inferred = parsed.get("inferred_understanding")
            if isinstance(raw_inferred, list):
                fallback_inferred.extend(str(x).strip() for x in raw_inferred if str(x).strip())
            elif raw_inferred:
                fallback_inferred.append(str(raw_inferred).strip())

            verified_demonstrated = cls._sanitize_demonstrated_concepts(
                parsed.get("concepts_demonstrated") or [],
                user_messages_by_id=user_messages_by_id,
                fallback_inferred=fallback_inferred,
            )
            # Merge with prior verified demonstrations so earlier verified mastery is preserved
            if prev_ckpt and isinstance(prev_ckpt.concepts_demonstrated, list):
                existing_concepts = {
                    str(item.get("concept", "")).lower()
                    for item in verified_demonstrated
                    if isinstance(item, dict)
                }
                for prior_item in prev_ckpt.concepts_demonstrated:
                    if isinstance(prior_item, dict):
                        c_name = str(prior_item.get("concept", "")).lower()
                        if c_name and c_name not in existing_concepts:
                            verified_demonstrated.append(prior_item)
                            existing_concepts.add(c_name)

            concepts_explained = cls._normalize_str_list(parsed.get("concepts_explained"))
            if prev_ckpt and isinstance(prev_ckpt.concepts_explained, list):
                seen_exp = {c.lower() for c in concepts_explained}
                for prior_c in prev_ckpt.concepts_explained:
                    if isinstance(prior_c, str) and prior_c.lower() not in seen_exp:
                        concepts_explained.append(prior_c)
                        seen_exp.add(prior_c.lower())

            misconceptions = cls._normalize_str_list(parsed.get("misconceptions"))
            key_discoveries = cls._normalize_str_list(parsed.get("key_discoveries"))
            learning_obj = (
                str(parsed.get("learning_objective") or "").strip()
                or session_obj.learning_objective
                or (prev_ckpt.learning_objective if prev_ckpt else "")
            )
            current_top = (
                str(parsed.get("current_topic") or "").strip()[:255]
                or session_obj.current_topic
                or (prev_ckpt.current_topic if prev_ckpt else "")
            )
            next_step = (
                str(parsed.get("recommended_next_step") or "").strip()
                or (prev_ckpt.recommended_next_step if prev_ckpt else "")
                or f"Continue exploring {current_top or 'the current study topic'}."
            )
            inferred_text = " ".join(part for part in fallback_inferred if part).strip()

            with transaction.atomic():
                locked_session = PwanimateStudySession.objects.select_for_update().get(id=session_obj.id)
                current_latest = (
                    PwanimateStudyCheckpoint.objects.filter(id=locked_session.latest_checkpoint_id).first()
                    if locked_session.latest_checkpoint_id
                    else None
                )
                # Out-of-order guard: never regress `latest_checkpoint` if a newer message checkpoint already won
                if (
                    current_latest
                    and current_latest.up_to_message_id is not None
                    and boundary_msg.id < current_latest.up_to_message_id
                ):
                    return current_latest

                checkpoint, _created = PwanimateStudyCheckpoint.objects.update_or_create(
                    session=locked_session,
                    up_to_message=boundary_msg,
                    defaults={
                        "learning_objective": learning_obj,
                        "current_topic": current_top,
                        "concepts_explained": concepts_explained[:25],
                        "concepts_demonstrated": verified_demonstrated[:25],
                        "inferred_understanding": inferred_text[:2000],
                        "misconceptions": misconceptions[:20],
                        "key_discoveries": key_discoveries[:20],
                        "recommended_next_step": next_step[:1000],
                        "document_state": doc_state,
                    },
                )
                locked_session.latest_checkpoint = checkpoint
                if learning_obj and not locked_session.learning_objective:
                    locked_session.learning_objective = learning_obj
                if current_top:
                    locked_session.current_topic = current_top[:255]
                locked_session.save(
                    update_fields=[
                        "latest_checkpoint",
                        "learning_objective",
                        "current_topic",
                        "updated_at",
                    ]
                )
                return checkpoint

        except Exception as exc:
            logger.warning(
                "Study checkpoint generation failed for session %s (preserving previous checkpoint): %s",
                session_obj.id,
                exc,
            )
            session_obj.refresh_from_db()
            return session_obj.latest_checkpoint
        finally:
            cache.delete(lock_key)

    @staticmethod
    def _normalize_str_list(val: Any) -> List[str]:
        if not isinstance(val, list):
            return []
        result = []
        for item in val:
            if isinstance(item, str) and item.strip():
                result.append(item.strip()[:300])
            elif isinstance(item, dict):
                text = str(item.get("concept") or item.get("text") or item.get("item") or "").strip()
                if text:
                    result.append(text[:300])
        return result

    @classmethod
    def _parse_checkpoint_json(
        cls,
        raw_content: str,
        session: PwanimateStudySession,
        prev_ckpt: Optional[PwanimateStudyCheckpoint],
        messages: List[PwanimateMessage],
    ) -> Dict[str, Any]:
        """Parse JSON from the LLM response or construct a deterministic structured fallback if non-JSON."""
        cleaned = raw_content.strip()
        fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", cleaned, re.IGNORECASE)
        if fence_match:
            cleaned = fence_match.group(1)
        else:
            brace_start = cleaned.find("{")
            brace_end = cleaned.rfind("}")
            if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
                cleaned = cleaned[brace_start : brace_end + 1]

        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                return data
        except (ValueError, TypeError):
            pass

        # Deterministic structured fallback when a mock/text provider returns plain prose
        last_user = next((m for m in reversed(messages) if m.role == "user"), None)
        last_asst = next((m for m in reversed(messages) if m.role == "assistant"), None)
        topic = session.current_topic or (
            ConversationService.derive_title(last_user.content) if last_user else "Study Session"
        )
        explained = []
        if last_asst and last_asst.content:
            first_sentence = re.split(r"(?<=[.!?])\s+", last_asst.content.strip(), maxsplit=1)[0]
            if first_sentence:
                explained.append(first_sentence[:200])
        return {
            "learning_objective": session.learning_objective or topic,
            "current_topic": topic[:255],
            "concepts_explained": explained,
            "concepts_demonstrated": [],
            "inferred_understanding": "Session in progress; awaiting explicit learner verification turns.",
            "misconceptions": [],
            "key_discoveries": [],
            "recommended_next_step": f"Review and practice key questions on {topic}.",
        }

    @classmethod
    def generate_session_summary(
        cls,
        user_or_session: Optional[Any] = None,
        session_id: Optional[Any] = None,
        session: Optional[PwanimateStudySession] = None,
        user: Optional[Any] = None,
        gateway: Optional[AIGateway] = None,
    ) -> StudySummaryResult:
        """
        Generate a reviewable Study Mode session summary from the transcript and latest checkpoint,
        and persist it as an assistant `PwanimateMessage` in the session's conversation.

        Does NOT automatically save to a Collection.
        """
        resolved_session: Optional[PwanimateStudySession] = None
        if isinstance(session, PwanimateStudySession):
            resolved_session = session
        elif isinstance(user_or_session, PwanimateStudySession):
            resolved_session = user_or_session
            if user is None and session_id is not None and hasattr(session_id, "is_authenticated"):
                user = session_id
        else:
            resolved_user = user or user_or_session
            resolved_session = cls.get_owned_session(resolved_user, session_id)

        if not resolved_session:
            raise LookupError("Study session not found.")

        conversation = resolved_session.conversation
        messages = list(
            conversation.messages.filter(role__in=["user", "assistant"]).order_by("created_at", "id")
        )
        if not messages:
            raise ValueError("Cannot generate a summary for an empty study session.")

        ckpt = resolved_session.latest_checkpoint
        if ckpt is None:
            try:
                ckpt = cls.generate_checkpoint(session=resolved_session, gateway=gateway)
            except Exception:
                ckpt = None
        ckpt_data = cls.serialize_checkpoint(ckpt) or {}

        transcript_excerpt = []
        for m in messages[-16:]:
            speaker = "Student" if m.role == "user" else "Pwanimate"
            transcript_excerpt.append(f"**{speaker}**: {(m.content or '').strip()[:600]}")

        title = conversation.title or resolved_session.current_topic or "Study Session"
        prompt = (
            f"Create a clear, structured Markdown Study Summary for the student's study session titled \"{title}\".\n\n"
            "Include these sections:\n"
            f"# Study Summary: {title}\n"
            "## 1. Learning Objective & Scope\n"
            "## 2. Key Concepts Covered\n"
            "## 3. Verified Student Progress\n"
            "(Distinguish concepts the student actively demonstrated from areas still being practiced.)\n"
            "## 4. Open Questions & Misconceptions to Revisit\n"
            "## 5. Recommended Next Steps\n\n"
            f"Learning Objective: {resolved_session.learning_objective or title}\n"
            f"Current Topic: {resolved_session.current_topic or title}\n"
            f"Latest Checkpoint Data: {json.dumps(ckpt_data)}\n\n"
            "Session Transcript Excerpt:\n" + "\n\n".join(transcript_excerpt)
        )

        ai_gateway = gateway or AIGateway()
        summary_markdown = ""
        try:
            llm_req = LLMRequest(
                task="general",
                messages=[ChatMessage(role="user", content=prompt)],
                system_instruction=(
                    "You are Pwanimate, producing an accurate, well-structured Markdown study summary "
                    "based strictly on the student's study session transcript and checkpoint."
                ),
                temperature=0.2,
                max_tokens=1500,
            )
            llm_resp = ai_gateway.generate(llm_req)
            summary_markdown = (llm_resp.content or "").strip()
        except Exception as exc:
            logger.warning("AI summary generation failed for session %s; using structured fallback: %s", resolved_session.id, exc)

        if not summary_markdown or not summary_markdown.lstrip().startswith("#"):
            explained_bullets = "\n".join(
                f"- {c}" for c in (ckpt_data.get("concepts_explained") or [resolved_session.current_topic or title])
            )
            demonstrated_items = ckpt_data.get("concepts_demonstrated") or []
            if demonstrated_items:
                demo_bullets = "\n".join(
                    f"- {d.get('concept') if isinstance(d, dict) else d}" for d in demonstrated_items
                )
            else:
                demo_bullets = "- Concepts introduced; independent practice exercises recommended next."
            misconceptions_list = ckpt_data.get("misconceptions") or []
            misc_bullets = (
                "\n".join(f"- {m}" for m in misconceptions_list)
                if misconceptions_list
                else "- No unresolved misconceptions recorded."
            )
            next_step = ckpt_data.get("recommended_next_step") or f"Continue practicing {resolved_session.current_topic or title}."
            body_extra = f"\n\n{summary_markdown}" if summary_markdown else ""
            summary_markdown = (
                f"# Study Summary: {title}\n\n"
                f"## 1. Learning Objective & Scope\n"
                f"{resolved_session.learning_objective or title}\n\n"
                f"## 2. Key Concepts Covered\n"
                f"{explained_bullets}\n\n"
                f"## 3. Verified Student Progress\n"
                f"{demo_bullets}\n\n"
                f"## 4. Open Questions & Misconceptions to Revisit\n"
                f"{misc_bullets}\n\n"
                f"## 5. Recommended Next Steps\n"
                f"{next_step}{body_extra}"
            ).strip()

        summary_sources = [
            {
                "source": "study_summary",
                "study_session_id": str(resolved_session.id),
                "title": f"Study Summary: {title}",
                "checkpoint_id": str(ckpt.id) if ckpt else None,
            }
        ]
        summary_msg = ConversationService.persist_assistant_message(
            conversation=conversation,
            content=summary_markdown,
            citations=[],
            sources=summary_sources,
        )
        ctx_state = dict(resolved_session.context_state or {})
        ctx_state["last_summary"] = {
            "message_id": summary_msg.id,
            "markdown": summary_markdown,
            "updated_at": timezone.now().isoformat(),
        }
        resolved_session.context_state = ctx_state
        resolved_session.last_active_at = timezone.now()
        resolved_session.save(update_fields=["context_state", "last_active_at", "updated_at"])
        return StudySummaryResult(resolved_session, summary_msg)

    @classmethod
    def update_session_summary(
        cls,
        session: PwanimateStudySession,
        user: Any,
        content: str,
        message_id: Optional[Any] = None,
    ) -> StudySummaryResult:
        """Update an existing assistant summary message in `session.conversation` after user review/edit."""
        if not session or session.user_id != getattr(user, "id", None):
            raise PermissionError("You do not have permission to edit this study summary.")
        clean_content = (content or "").strip()
        if not clean_content:
            raise ValueError("Summary content cannot be empty.")

        target_msg: Optional[PwanimateMessage] = None
        if message_id is not None:
            try:
                target_msg = PwanimateMessage.objects.filter(
                    id=int(message_id),
                    conversation_id=session.conversation_id,
                    role="assistant",
                ).first()
            except (ValueError, TypeError):
                target_msg = None

        if target_msg is None:
            last_summary = (session.context_state or {}).get("last_summary") or {}
            last_msg_id = last_summary.get("message_id")
            if last_msg_id:
                target_msg = PwanimateMessage.objects.filter(
                    id=int(last_msg_id),
                    conversation_id=session.conversation_id,
                    role="assistant",
                ).first()

        if target_msg is None:
            target_msg = ConversationService.persist_assistant_message(
                conversation=session.conversation,
                content=clean_content,
            )
        else:
            target_msg.content = clean_content
            target_msg.save(update_fields=["content"])

        ctx_state = dict(session.context_state or {})
        ctx_state["last_summary"] = {
            "message_id": target_msg.id,
            "markdown": clean_content,
            "updated_at": timezone.now().isoformat(),
        }
        session.context_state = ctx_state
        session.last_active_at = timezone.now()
        session.save(update_fields=["context_state", "last_active_at", "updated_at"])
        return StudySummaryResult(session, target_msg)


class StudyCollectionService:
    """
    Minimal Collection service supporting Study Mode summary review & saving.
    Enforces ownership and shared-collection `can_edit=True` permissions, and reuses
    `create_generated_resource` + `CollectionItem` without duplicating file storage.
    """

    @staticmethod
    def list_editable_collections(user: Any) -> List[Dict[str, Any]]:
        """List collections owned by `user` or shared with `user` with `can_edit=True`."""
        if not user or not getattr(user, "is_authenticated", False):
            return []
        qs = (
            Collection.objects.filter(
                Q(owner=user)
                | Q(shares__shared_with=user, shares__can_edit=True)
            )
            .select_related("owner")
            .distinct()
            .order_by("-updated_at", "-created_at")
        )
        results = []
        for col in qs[:50]:
            doc_count = col.items.count()
            results.append({
                "id": col.id,
                "name": col.name,
                "slug": col.slug,
                "description": col.description,
                "visibility": col.visibility,
                "is_owner": col.owner_id == user.id,
                "document_count": doc_count,
                "item_count": doc_count,
                "updated_at": col.updated_at.isoformat() if col.updated_at else None,
            })
        return results

    @staticmethod
    def user_can_edit_collection(user: Any, collection: Optional[Collection]) -> bool:
        """Return True iff `user` owns `collection` or has a `CollectionShare` with `can_edit=True`."""
        if not user or not getattr(user, "is_authenticated", False) or not collection:
            return False
        if collection.owner_id == user.id:
            return True
        return CollectionShare.objects.filter(
            collection=collection,
            shared_with=user,
            can_edit=True,
        ).exists()

    @staticmethod
    @transaction.atomic
    def create_collection(
        user: Any, name: str, description: str = "", visibility: str = "private"
    ) -> Collection:
        """Create a new private (by default) Collection owned by `user` with a unique slug."""
        if not user or not getattr(user, "is_authenticated", False):
            raise PermissionError("Authentication required.")
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("Collection name is required.")
        if len(clean_name) > 200:
            raise ValueError("Collection name cannot exceed 200 characters.")
        if visibility not in {"private", "public", "unlisted"}:
            visibility = "private"

        base_slug = slugify(f"{user.username}-{clean_name}")[:230] or f"collection-{user.id}"
        slug = base_slug
        suffix = 2
        while Collection.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{suffix}"
            suffix += 1

        return Collection.objects.create(
            name=clean_name,
            slug=slug,
            description=(description or "").strip(),
            owner=user,
            visibility=visibility,
        )

    @classmethod
    @transaction.atomic
    def save_summary_to_collection(
        cls,
        user: Any,
        session: PwanimateStudySession,
        message: Optional[PwanimateMessage] = None,
        message_id: Optional[Any] = None,
        summary_markdown: str = "",
        collection_id: Optional[Any] = None,
        new_collection_name: Optional[str] = None,
        new_collection_description: str = "",
        new_collection_visibility: str = "private",
        file_format: str = "pdf",
        notes: str = "",
    ) -> Dict[str, Any]:
        """
        Save a reviewed Study Mode summary `PwanimateMessage` into an existing or newly
        created `Collection` by reusing `create_generated_resource` and `CollectionItem`.

        Guarantees:
        - Enforces session & message ownership.
        - Enforces collection ownership or `CollectionShare(can_edit=True)`.
        - Keeps the generated `Document` private (`visibility="private"`).
        - Idempotent on repeated saves (reuses existing generated `Document` and `CollectionItem`).
        """
        if not user or not getattr(user, "is_authenticated", False):
            raise PermissionError("Authentication required.")
        if session.user_id != user.id:
            raise PermissionError("You do not have permission to save this study summary.")

        resolved_msg = message
        if resolved_msg is None and message_id is not None:
            try:
                resolved_msg = PwanimateMessage.objects.filter(
                    id=int(message_id),
                    conversation_id=session.conversation_id,
                    role="assistant",
                ).first()
            except (ValueError, TypeError):
                resolved_msg = None
            if resolved_msg is None:
                raise LookupError("Summary message not found in this study session.")

        if resolved_msg is None:
            last_summary = (session.context_state or {}).get("last_summary") or {}
            last_id = last_summary.get("message_id")
            if last_id:
                resolved_msg = PwanimateMessage.objects.filter(
                    id=int(last_id),
                    conversation_id=session.conversation_id,
                    role="assistant",
                ).first()

        if resolved_msg is not None and summary_markdown and summary_markdown.strip():
            if resolved_msg.content != summary_markdown.strip():
                resolved_msg.content = summary_markdown.strip()
                resolved_msg.save(update_fields=["content"])
        elif resolved_msg is None:
            if not summary_markdown or not summary_markdown.strip():
                raise ValueError("Summary content or message_id is required.")
            resolved_msg = ConversationService.persist_assistant_message(
                conversation=session.conversation,
                content=summary_markdown.strip(),
            )
            ctx_state = dict(session.context_state or {})
            ctx_state["last_summary"] = {
                "message_id": resolved_msg.id,
                "markdown": summary_markdown.strip(),
                "updated_at": timezone.now().isoformat(),
            }
            session.context_state = ctx_state
            session.save(update_fields=["context_state", "updated_at"])

        if resolved_msg.conversation_id != session.conversation_id:
            raise PermissionError("You do not have permission to save this study summary.")
        if resolved_msg.role != "assistant":
            raise ValueError("Only assistant summary messages can be saved to a collection.")

        fmt = (file_format or "pdf").strip().lower()
        if fmt not in {"pdf", "docx"}:
            raise ValueError("Choose PDF or DOCX format.")

        collection: Optional[Collection] = None
        collection_created = False
        if new_collection_name and str(new_collection_name).strip():
            collection = cls.create_collection(
                user=user,
                name=str(new_collection_name).strip(),
                description=new_collection_description,
                visibility=new_collection_visibility or "private",
            )
            collection_created = True
        elif collection_id is not None:
            try:
                collection = Collection.objects.filter(id=int(collection_id)).first()
            except (ValueError, TypeError):
                collection = None
            if not collection:
                raise LookupError("Collection not found.")
            if not cls.user_can_edit_collection(user, collection):
                raise PermissionError("You do not have permission to edit this collection.")
        else:
            raise ValueError("Specify either an existing collection_id or a new_collection_name.")

        from pwanimate.services.generated_documents import create_generated_resource

        document, document_file, doc_created = create_generated_resource(
            message=resolved_msg,
            user=user,
            file_format=fmt,
        )
        # Ensure saving to a collection never makes a private generated document public
        if document.visibility != "private":
            document.visibility = "private"
            document.save(update_fields=["visibility"])

        default_notes = (
            notes.strip()
            if notes and notes.strip()
            else f"Study Mode summary from session: {session.current_topic or session.conversation.title or 'Study Session'}"
        )
        max_order = (
            CollectionItem.objects.filter(collection=collection).aggregate(m=Max("order")).get("m")
            or 0
        )
        item, item_created = CollectionItem.objects.get_or_create(
            collection=collection,
            document=document,
            defaults={
                "added_by": user,
                "notes": default_notes,
                "order": max_order + 1,
            },
        )
        if not item_created and notes and notes.strip() and item.notes != notes.strip():
            item.notes = notes.strip()
            item.save(update_fields=["notes"])

        return {
            "created": bool(collection_created or item_created),
            "collection_name": collection.name,
            "collection_url": f"/documents/collections/{collection.slug}/",
            "collection": {
                "id": collection.id,
                "name": collection.name,
                "slug": collection.slug,
                "visibility": collection.visibility,
                "created": collection_created,
                "document_count": collection.items.count(),
            },
            "collection_item": {
                "id": item.id,
                "created": item_created,
                "notes": item.notes,
                "added_at": item.added_at.isoformat() if item.added_at else None,
            },
            "document": {
                "id": document.id,
                "share_id": str(document.share_id),
                "title": document.title,
                "format": fmt,
                "visibility": document.visibility,
                "created": doc_created,
                "file_id": document_file.id if document_file else None,
            },
        }
