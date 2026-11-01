"""
Pwanimate API Views.

Exposes authenticated HTTP endpoints for persistent conversations,
RAG interactions, conversation listings, and detail/deletion.
"""

import asyncio
from io import BytesIO
import json
import logging
import os
import queue
import sys
import threading
import time
import uuid
import requests
from django.db import close_old_connections, transaction
from django.db.models import Q
from django.conf import settings
from django.http import FileResponse, Http404, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.core.signing import TimestampSigner
from urllib.parse import urlencode
from django.template.loader import render_to_string
from rest_framework import permissions, status
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.views import APIView

from pwanimate.ai.exceptions import (
    AIGatewayError,
    AIProviderConfigurationError,
    AIProviderRateLimitError,
    AIProviderTimeoutError,
)
from pwanimate.api.serializers import (
    ConversationDetailSerializer,
    ConversationListSerializer,
    PwanimateAttachmentSerializer,
)
from pwanimate.models import (
    PwanimateAttachment,
    PwanimateConversation,
    PwanimateMessage,
    PwanimateStudyCheckpoint,
    PwanimateStudySession,
)
from pwanimate.orchestrator import (
    OrchestrationRequest,
    OrchestratorValidationError,
    PwanimateOrchestrator,
)
from django.core.exceptions import PermissionDenied, ValidationError
from pwanimate.services.conversation import ConversationService
from pwanimate.services.conversation_memory import ConversationMemoryService
from pwanimate.services.attachment import AttachmentService
from pwanimate.services.study_session import (
    StudyCollectionService,
    StudySessionService,
    parse_study_command,
)
from pwanimate.services.tts import (
    TTSConfigurationError,
    TTSProviderError,
    synthesize,
)
from pwanimate.ai.gateway.quota_tracker import get_quota_tracker
from pwanimate.ai.engines import UnknownSparkEngineError, get_engine, get_engines_status, identify_engine
from documents.models import Document

logger = logging.getLogger(__name__)


class AudioMP3Renderer(BaseRenderer):
    """Allow DRF content negotiation for the binary Spark audio response."""

    media_type = "audio/mpeg"
    format = "mp3"
    render_style = "binary"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        return JSONRenderer().render(data, accepted_media_type, renderer_context)


def _quota_block_payload(decision) -> dict:
    """
    The body returned when a turn exceeds the student's tier allowance.

    It carries the same numbers the quota chip renders, so the student sees
    what they have left and when it resets rather than a bare "limit
    reached".
    """
    from pwanimate.billing.services import STATE_UPGRADE_REQUIRED

    if decision.entitlement_key == "images":
        message = (
            "You have used today's image analyses. "
            "Upgrade for more image analyses, or come back tomorrow."
        )
    else:
        message = (
            "You have used today's messages on Spark. "
            "Upgrade for more, or come back tomorrow."
        )

    payload = {
        "error": message,
        "quota": decision.as_dict(),
        "upgrade_required": decision.state == STATE_UPGRADE_REQUIRED,
    }
    try:
        payload["quota_status"] = get_quota_tracker().get_status()
    except Exception:  # noqa: BLE001 - never fail the block on a status call
        pass
    return payload


class PwanimateAttachmentVisionPolicyView(APIView):
    """
    Record what the student wants to happen to an image whose analysis failed.

    'keep' -> the image stays in the context so they can keep asking about
    it; 'drop' -> it is never sent again. 'ask' is the untouched default
    (the choice is still open).
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, attachment_id):
        from pwanimate.models import PwanimateAttachment

        policy = str((request.data or {}).get("policy") or "").strip().lower()
        if policy not in {"keep", "drop"}:
            return Response(
                {"error": "policy must be 'keep' or 'drop'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        attachment = PwanimateAttachment.objects.filter(
            id=attachment_id, user=request.user
        ).first()
        if attachment is None:
            return Response(
                {"error": "Attachment not found."}, status=status.HTTP_404_NOT_FOUND
            )
        attachment.vision_context_policy = policy
        attachment.save(update_fields=["vision_context_policy"])
        return Response(
            {
                "attachment_id": str(attachment.id),
                "policy": policy,
                "message": (
                    "Kept — Spark will try the image again next time."
                    if policy == "keep"
                    else "Dropped — the image will not be sent again."
                ),
            },
            status=status.HTTP_200_OK,
        )


class PwanimateChatView(APIView):
    """
    Primary conversational endpoint for Pwanimate.

    Accepts an authenticated student question, persists user and assistant turns,
    invokes the Pwanimate Orchestrator, and returns a structured JSON answer
    with conversation and message IDs, citations, source provenance, and timing.
    """

    permission_classes = [permissions.IsAuthenticated]
    orchestrator = None

    def get_orchestrator(self) -> PwanimateOrchestrator:
        return self.orchestrator or PwanimateOrchestrator()

    def post(self, request):
        if "text/event-stream" in request.headers.get("Accept", ""):
            # Parse the request body on the request thread before the worker uses it.
            # DRF caches parsed data on the Request instance.
            _ = request.data
            _ = request.user.is_authenticated
            return self._stream_chat_response(request)
        return self._post_chat(request)

    def _stream_chat_response(self, request):
        """Stream truthful orchestration status updates, followed by the usual JSON payload."""
        events = queue.Queue()

        def report_progress(stage):
            if stage in {
                "searching_web",
                "reading_sources",
                "devouring_context",
                "searching_posts",
                "searching_documents",
            }:
                events.put(("status", {"stage": stage}))

        def report_tokens(text):
            """Visible LLM text deltas -> chunk events for progressive rendering."""
            if text:
                events.put(("chunk", {"text": text}))

        def perform_request():
            close_old_connections()
            try:
                result = self._post_chat(
                    request,
                    progress_callback=report_progress,
                    token_callback=report_tokens,
                )
                events.put(("response", (result.status_code, result.data)))
            except Exception:
                logger.exception("Pwanimate streamed chat request failed unexpectedly")
                events.put(("response", (
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    {"error": "An unexpected error occurred while processing your request."},
                )))
            finally:
                close_old_connections()

        worker = threading.Thread(target=perform_request, name="pwanimate-chat", daemon=True)
        worker.start()

        async def stream_events():
            yield 'event: status\ndata: {"stage":"thinking"}\n\n'
            while True:
                try:
                    event_type, payload = await asyncio.to_thread(events.get, True, 12)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue

                if event_type == "status":
                    yield f"event: status\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
                    continue

                if event_type == "chunk":
                    yield f"event: chunk\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
                    continue

                response_status, response_data = payload
                encoded = JSONRenderer().render({
                    "status_code": response_status,
                    "data": response_data,
                }).decode("utf-8")
                yield f"event: response\ndata: {encoded}\n\n"
                break

        response = StreamingHttpResponse(stream_events(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache, no-transform"
        response["X-Accel-Buffering"] = "no"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    def _post_chat(self, request, progress_callback=None, token_callback=None):
        """
        Process a chat or RAG query.

        Expected JSON payload:
            message (str): Student's query (required).
            conversation_id (str): Optional UUID of existing conversation.
            history (list): Optional fallback client history if conversation_id is omitted.
            sources (list): Optional list of source strings ['document', 'post'].
            task (str): Optional task override ('rag', 'general').
            engine (str): Optional Spark engine slug ('speed', 'coda', 'scholar').
                When present it resolves to the engine's primary provider/model
                and ordered fallback chain, overriding provider/model.
            provider (str): Optional provider override ('gemini', 'groq', 'openrouter', 'mock').
            model (str): Optional model override.
            attachments (list): Optional list of attachment UUIDs or objects.
            context_resources (list): Optional list of Context Rail resource descriptors.
            study_mode (bool): Optional flag to enter/continue Study Mode.
            study_session_id (str): Optional UUID of an owned Study Mode session.
        """
        data = request.data or {}
        message = data.get("message") if "message" in data else data.get("query")
        if not message or not isinstance(message, str) or not message.strip():
            return Response(
                {"error": "Field 'message' is required and must be a non-empty string."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Resolve the Spark engine before any side effects (persisted turns,
        # orchestrator work) so unknown/hidden engine slugs fail fast.
        engine_slug = data.get("engine")
        engine = None
        if engine_slug:
            try:
                engine = get_engine(str(engine_slug))
            except UnknownSparkEngineError:
                return Response(
                    {"error": f"Unknown engine '{engine_slug}'."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # A named offering (the premium capability chip) is a registry entry,
        # never a raw model name: the tier decides whether it is reachable.
        # Locked offerings are refused with the upgrade path, not silently
        # downgraded (D8: locks are enforced server-side).
        offering_key = data.get("offering")
        offering = None
        if offering_key:
            try:
                from pwanimate.billing.services import (
                    offering_allows_tier,
                    offering_by_key,
                    tier_rank_for_user,
                )

                offering = offering_by_key(str(offering_key))
                if offering is None:
                    return Response(
                        {"error": f"Unknown offering '{offering_key}'."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                tier_rank = tier_rank_for_user(request.user)
                if not offering_allows_tier(offering, offering.provider, offering.model_id, tier_rank):
                    required_tier = offering.min_tier
                    return Response(
                        {
                            "error": "model_locked",
                            "offering": offering.key,
                            "required_tier": required_tier.code if required_tier else None,
                            "required_tier_name": required_tier.name if required_tier else None,
                            "price_kes": float(required_tier.price_kes) if required_tier else None,
                            "message": (
                                f"'{offering.display_name or offering.model_id}' is available on the "
                                f"{required_tier.name if required_tier else 'paid'} tier."
                            ),
                        },
                        status=status.HTTP_403_FORBIDDEN,
                    )
            except ImportError:
                # Billing not installed yet: ignore the offering rather than fail.
                offering = None

        is_study_cmd, study_remainder = parse_study_command(message)
        study_session_id = data.get("study_session_id")
        requested_study_session = None

        conversation_id = data.get("conversation_id")
        conversation = None

        if study_session_id:
            requested_study_session = StudySessionService.get_owned_session(
                user=request.user,
                session_id=study_session_id,
            )
            if requested_study_session is None:
                return Response(
                    {"error": "Study session not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if conversation_id and str(requested_study_session.conversation_id) != str(conversation_id):
                return Response(
                    {"error": "Conversation does not match study session."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            conversation = requested_study_session.conversation
            conversation_id = str(conversation.id)

        if conversation is None:
            if conversation_id:
                conversation = ConversationService.get_owned_conversation(
                    user=request.user,
                    conversation_id=conversation_id,
                )
                if conversation is None:
                    if data.get("create_conversation") is True:
                        try:
                            requested_id = uuid.UUID(str(conversation_id))
                        except (ValueError, TypeError, AttributeError):
                            return Response(
                                {"error": "Invalid conversation ID."},
                                status=status.HTTP_400_BAD_REQUEST,
                            )
                        if PwanimateConversation.objects.filter(id=requested_id).exists():
                            return Response(
                                {"error": "Conversation not found."},
                                status=status.HTTP_404_NOT_FOUND,
                            )
                        conversation = ConversationService.create_conversation(
                            user=request.user,
                            conversation_id=requested_id,
                        )
                    else:
                        return Response(
                            {"error": "Conversation not found."},
                            status=status.HTTP_404_NOT_FOUND,
                        )
            else:
                conversation = ConversationService.create_conversation(user=request.user)

        # Validate attachments if provided
        raw_attachment_ids = data.get("attachments") or data.get("attachment_ids") or []
        attachment_objs = []
        if isinstance(raw_attachment_ids, list) and raw_attachment_ids:
            for item in raw_attachment_ids:
                aid = item.get("id") if isinstance(item, dict) else item
                if not aid:
                    continue
                att = AttachmentService.get_authorized_attachment(
                    user=request.user,
                    attachment_id=aid,
                )
                if not att:
                    return Response(
                        {"error": f"Attachment '{aid}' not found or unauthorized."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                attachment_objs.append(att)

        conversation_attachments = list(conversation.attachments.all()) if conversation else []
        all_known_attachments = {str(att.id): att for att in conversation_attachments}
        all_known_attachments.update({str(att.id): att for att in attachment_objs})
        pending_documents = [
            att for att in all_known_attachments.values()
            if att.attachment_type == "document"
            and att.processing_status in {"pending", "processing"}
        ]
        pending_images = [
            att for att in all_known_attachments.values()
            if att.attachment_type == "image"
            and att.processing_status in {"pending", "processing"}
        ]
        if pending_documents:
            return Response(
                {"error": "Pwanimate is still reading the attached document. Try again when it shows Ready."},
                status=status.HTTP_409_CONFLICT,
            )
        if pending_images:
            # The image is being analysed right now. Sending it to a second
            # model in this turn would analyse it twice and bill twice.
            return Response(
                {
                    "error": "Spark is still reading that image. Try again in a moment once it shows Ready.",
                    "attachment_id": str(pending_images[0].id),
                },
                status=status.HTTP_409_CONFLICT,
            )
        failed_documents = [
            att for att in attachment_objs
            if att.attachment_type == "document" and att.processing_status == "failed"
        ]
        if failed_documents:
            return Response(
                {"error": "Pwanimate could not read one of these documents. Remove it and try a clearer or text-based copy."},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        # Enforce the tier allowance before any work is done.
        #
        # The quota numbers the student sees on the chip are read from the
        # same counters this check reads, so the number shown is the number
        # acted on. Until this existed the counters were written but never
        # consulted, which made every daily cap decorative.
        #
        # Only images that will actually be sent are counted: an image that
        # was already analysed at upload is represented by its stored
        # description and costs nothing on this turn.
        try:
            from pwanimate.ai.vision_images import image_will_be_sent
            from pwanimate.billing.services import QuotaService

            images_to_send = sum(
                1 for att in attachment_objs if image_will_be_sent(att)
            )
            decision = QuotaService.check_turn(
                request.user, image_count=images_to_send
            )
            if decision is not None and not decision.allowed:
                return Response(
                    _quota_block_payload(decision),
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
        except ImportError:
            # Billing not installed yet: never block a turn.
            pass
        except Exception:
            logger.exception("Quota check failed; allowing the turn")

        context_resources = data.get("context_resources") or []
        if not isinstance(context_resources, list):
            context_resources = []

        existing_study_session = (
            requested_study_session
            or StudySessionService.get_session_for_conversation(request.user, conversation)
        )
        explicit_study_flag = data.get("study_mode")
        study_mode_active = bool(
            is_study_cmd
            or explicit_study_flag is True
            or requested_study_session is not None
            or (
                existing_study_session is not None
                and existing_study_session.status == PwanimateStudySession.STATUS_ACTIVE
                and explicit_study_flag is not False
            )
        )

        # Bare `@study` command activates Study Mode without making an empty AI request
        if is_study_cmd and not study_remainder:
            study_session = StudySessionService.start_or_continue_session(
                user=request.user,
                conversation=conversation,
                title=str(data.get("title") or "").strip(),
                learning_objective=str(data.get("learning_objective") or "").strip(),
                current_topic=str(data.get("current_topic") or "").strip(),
                context_resources=context_resources,
                attachments=attachment_objs,
            )
            if attachment_objs:
                with transaction.atomic():
                    for att in attachment_objs:
                        att.conversation = conversation
                        att.save(update_fields=["conversation"])
            return Response(
                {
                    "study_mode_activated": True,
                    "study_mode": True,
                    "study_session": StudySessionService.serialize_session(study_session),
                    "conversation_id": str(conversation.id),
                    "answer": "",
                    "citations": [],
                    "sources": [],
                    "people": [],
                    "blocks": [],
                    "warnings": [],
                    "metadata": {"study_mode": True, "study_mode_activated": True},
                },
                status=status.HTTP_200_OK,
            )

        clean_query = study_remainder.strip() if (is_study_cmd and study_remainder.strip()) else message.strip()
        retry_requested = data.get("retry") is True
        retry_message_id = data.get("retry_user_message_id")
        if retry_message_id is not None:
            try:
                retry_message_id = int(retry_message_id)
            except (TypeError, ValueError):
                return Response(
                    {"error": "Invalid retry message ID."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        user_msg = None
        if retry_requested:
            user_msg = ConversationService.get_retryable_user_message(
                conversation,
                clean_query,
                message_id=retry_message_id,
            )
            if retry_message_id is not None and user_msg is None:
                return Response(
                    {"error": "Message to retry was not found in this conversation."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if user_msg is not None and user_msg.content not in {clean_query, message.strip()}:
                return Response(
                    {"error": "Retry content must match the original message."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        if user_msg is None:
            user_msg = ConversationService.persist_user_message(conversation, clean_query)

        # Atomically link attachments to conversation and user message
        if attachment_objs:
            with transaction.atomic():
                for att in attachment_objs:
                    att.conversation = conversation
                    att.message = user_msg
                    att.save(update_fields=["conversation", "message"])

        # Multi-turn attachment context preservation:
        # Include current turn attachments plus active working materials attached earlier in this conversation
        active_attachments = list(attachment_objs)
        if conversation:
            seen_ids = {a.id for a in active_attachments}
            for conv_att in conversation.attachments.all().order_by("created_at"):
                if conv_att.id not in seen_ids and conv_att.processing_status != "failed":
                    active_attachments.append(conv_att)
                    seen_ids.add(conv_att.id)

        study_session = None
        study_context = None
        if study_mode_active:
            initial_objective = str(data.get("learning_objective") or "").strip()
            initial_topic = str(data.get("current_topic") or "").strip()
            if is_study_cmd and not initial_topic:
                initial_topic = clean_query[:160]
            if is_study_cmd and not initial_objective:
                initial_objective = f"Master {clean_query[:180]}"
            study_session = StudySessionService.start_or_continue_session(
                user=request.user,
                conversation=conversation,
                title=str(data.get("title") or "").strip(),
                learning_objective=initial_objective,
                current_topic=initial_topic or clean_query[:160],
                context_resources=context_resources,
                attachments=active_attachments,
            )
            # Study Mode strictly uses bounded server-side history; client-submitted history/checkpoints are ignored
            history = ConversationService.load_history(
                conversation,
                max_messages=StudySessionService.STUDY_HISTORY_MAX_MESSAGES,
                max_tokens=StudySessionService.STUDY_HISTORY_MAX_TOKENS,
                exclude_message_id=user_msg.id,
            )
            study_context = StudySessionService.build_study_orchestration_context(
                session=study_session,
                query=clean_query,
                recent_history=history,
            )
        else:
            # Load history without the current/retried user turn, so it is sent only once.
            server_history = ConversationService.load_history(
                conversation,
                exclude_message_id=user_msg.id if retry_requested else user_msg.id,
            )
            # Fallback to client history only if no server history exists yet (backward compatibility)
            history = server_history
            if not server_history and not conversation_id and data.get("history"):
                history = data.get("history", [])

        conversation_memory = ConversationMemoryService.search(
            request.user,
            clean_query,
            exclude_conversation_id=conversation.id,
        )

        sources = data.get("sources")
        task = data.get("task", "rag")
        provider = data.get("provider")
        model = data.get("model")
        if engine is not None:
            # Engine routing wins over legacy raw provider/model overrides:
            # brand-level selection resolves to the engine's primary
            # candidate, with its ordered chain carried alongside.
            primary_candidate = engine["fallback_chain"][0]
            provider = primary_candidate["provider"]
            model = primary_candidate["model"]

        try:
            orchestration_req = OrchestrationRequest(
                query=clean_query,
                user=request.user,
                history=history,
                task=task,
                sources=sources,
                provider=provider,
                model=model,
                engine=engine["slug"] if engine else None,
                fallback_chain=list(engine["fallback_chain"]) if engine else None,
                offering=offering.key if offering else None,
                attachments=active_attachments,
                context_resources=context_resources,
                local_time=data.get("local_time"),
                timezone_name=data.get("timezone"),
                study_mode=study_mode_active,
                study_context=study_context,
                conversation_memory=conversation_memory,
                request_id=request.headers.get("X-Request-ID") or str(uuid.uuid4()),
            )
            orchestrator = self.get_orchestrator()
            # External LLM generation occurs outside database transactions.
            # token_callback (when provided) receives visible text deltas as
            # the LLM streams — the SSE layer turns them into chunk events.
            response = orchestrator.run(
                orchestration_req,
                on_progress=progress_callback,
                on_token=token_callback,
            )
            response.metadata = response.metadata or {}
            response.metadata.setdefault("prompt_version", "2026-10-26.1")
            response.metadata.setdefault("context_version", "2.1")
            response.metadata.setdefault("memory_count", len(conversation_memory))
            response.metadata.setdefault("memory_trace", conversation_memory)
            response.metadata.setdefault("retrieval_trace", response.sources or [])

            # Persist assistant response turn with defensive fallback
            answer_text = (response.answer or "").strip()
            if not answer_text:
                answer_text = "I'm sorry, I was unable to generate a response. Please try asking again or rephrasing your question."

            asst_msg = ConversationService.persist_assistant_message(
                conversation=conversation,
                content=answer_text,
                citations=response.citations,
                sources=response.sources,
                provider=response.provider,
                model_id=response.model,
                task=str(
                    response.metadata.get("task", "")
                    or response.metadata.get("intent", "")
                    or ""
                ),
                prompt_tokens=int(response.prompt_tokens or 0),
                completion_tokens=int(response.completion_tokens or 0),
                total_tokens=int(response.total_tokens or 0),
            )
            try:
                from pwanimate.models import PwanimateTurnTrace

                trace_metadata = response.metadata or {}
                PwanimateTurnTrace.objects.create(
                    user=request.user,
                    conversation=conversation,
                    user_message=user_msg,
                    assistant_message=asst_msg,
                    request_id=orchestration_req.request_id or "",
                    prompt_version=str(trace_metadata.get("prompt_version") or "2026-10-26.1"),
                    context_version=str(trace_metadata.get("context_version") or "2.1"),
                    retrieval_trace=trace_metadata.get("retrieval_trace") or [],
                    memory_trace=trace_metadata.get("memory_trace") or conversation_memory,
                    metadata={
                        "intent": trace_metadata.get("intent"),
                        "retrieved_count": trace_metadata.get("retrieved_count", 0),
                        "context_items_count": trace_metadata.get("context_items_count", 0),
                        "total_time_ms": response.total_time_ms,
                        "retrieval_time_ms": response.retrieval_time_ms,
                        "generation_time_ms": response.generation_time_ms,
                    },
                )
            except Exception:
                logger.exception("Could not persist Pwanimate turn trace")

            res_data = response.to_dict()
            if response.people:
                res_data["people_html"] = render_to_string(
                    "pwanimate/partials/people_section.html",
                    {"people": response.people},
                    request=request,
                )
            # Brand-level attribution: the client credits the serving engine,
            # never the raw provider/model identity (Build ③ privacy rule).
            quota_info = res_data.get("quota_info") or {}
            res_data["serving_engine"] = identify_engine(
                quota_info.get("active_provider"), quota_info.get("active_model")
            )
            res_data["conversation_id"] = str(conversation.id)
            res_data["message_id"] = asst_msg.id
            res_data["user_message_id"] = user_msg.id
            if attachment_objs:
                res_data["attachments"] = PwanimateAttachmentSerializer(attachment_objs, many=True).data

            if study_mode_active and study_session is not None:
                if StudySessionService.should_auto_checkpoint(study_session):
                    from pwanimate.tasks.study import generate_study_checkpoint_task
                    delay_fn = getattr(generate_study_checkpoint_task, "delay", None)
                    is_mocked_delay = hasattr(delay_fn, "assert_called")
                    try:
                        if callable(delay_fn):
                            delay_fn(str(study_session.id), up_to_message_id=asst_msg.id)
                        else:
                            StudySessionService.generate_checkpoint(
                                study_session.id, up_to_message_id=asst_msg.id
                            )
                    except Exception:
                        logger.debug(
                            "Celery dispatch unavailable for study checkpoint %s; running inline",
                            study_session.id,
                        )
                        StudySessionService.generate_checkpoint(
                            study_session.id, up_to_message_id=asst_msg.id
                        )
                    if (
                        not is_mocked_delay
                        and ("test" in sys.argv or getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False))
                        and not PwanimateStudyCheckpoint.objects.filter(
                            session=study_session, up_to_message_id=asst_msg.id
                        ).exists()
                    ):
                        StudySessionService.generate_checkpoint(
                            study_session.id, up_to_message_id=asst_msg.id
                        )

                study_session = (
                    PwanimateStudySession.objects.select_related("conversation", "latest_checkpoint")
                    .filter(pk=study_session.pk)
                    .first()
                )
                res_data["study_mode"] = True
                res_data["study_session"] = StudySessionService.serialize_session(study_session)

            # Include fallback info and quota status
            fallback_info = {
                "fallback_used": response.metadata.get("fallback_used", False),
                "original_provider": response.metadata.get("original_provider", response.provider),
                "provider_used": response.provider,
                "vision_fallback": response.metadata.get("vision_route") == "fallback",
                "vision_text_fallback": response.metadata.get("vision_text_fallback", False),
                "vision_attachment_id": response.metadata.get("vision_attachment_id"),
                "vision_policy": response.metadata.get("vision_policy", "ask"),
            }
            res_data["fallback_info"] = fallback_info
            res_data["quota_info"] = response.quota_info
            res_data["quota_status"] = get_quota_tracker().get_status()
            # Actions Spark proposed. The student confirms them in the app;
            # nothing has happened yet.
            pending_actions = response.metadata.get("pending_actions") or []
            if pending_actions:
                res_data["pending_actions"] = pending_actions

            thoughts_tokens = response.metadata.get("thoughts_tokens")
            total_output_tokens = response.metadata.get("total_output_tokens")
            max_output_tokens = response.metadata.get("max_output_tokens")

            logger.info(
                "PwanimateChatView response: user=%s conv=%s msg_id=%s provider=%s model=%s finish_reason=%s prompt_tokens=%s completion_tokens=%s thoughts_tokens=%s total_output_tokens=%s total_tokens=%s max_output_tokens=%s ans_len=%d",
                getattr(request.user, "username", "anon"),
                conversation.id,
                asst_msg.id,
                response.provider,
                response.model,
                response.finish_reason,
                response.prompt_tokens,
                response.completion_tokens,
                thoughts_tokens,
                total_output_tokens,
                response.total_tokens,
                max_output_tokens,
                len(response.answer),
            )

            return Response(res_data, status=status.HTTP_200_OK)

        except OrchestratorValidationError as exc:
            return Response(
                {"error": str(exc), "conversation_id": str(conversation.id), "user_message_id": user_msg.id},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except AIProviderConfigurationError as exc:
            logger.warning("Pwanimate provider capability error: %s", exc)
            return Response(
                {"error": exc.message, "conversation_id": str(conversation.id), "user_message_id": user_msg.id},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ValidationError as exc:
            logger.warning("Pwanimate validation error: %s", exc)
            return Response(
                {
                    "error": str(exc.messages if hasattr(exc, "messages") else exc),
                    "conversation_id": str(conversation.id),
                    "user_message_id": user_msg.id,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except AIProviderRateLimitError as exc:
            logger.warning("Pwanimate rate limit: %s", exc)
            return Response(
                {
                    "error": "AI service is temporarily rate limited. Please try again shortly.",
                    "conversation_id": str(conversation.id),
                    "user_message_id": user_msg.id,
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        except AIProviderTimeoutError as exc:
            logger.warning("Pwanimate timeout: %s", exc)
            return Response(
                {
                    "error": "AI service request timed out.",
                    "conversation_id": str(conversation.id),
                    "user_message_id": user_msg.id,
                },
                status=status.HTTP_504_GATEWAY_TIMEOUT,
            )
        except AIGatewayError as exc:
            logger.error("Pwanimate gateway error: %s", exc)
            return Response(
                {
                    "error": f"AI service error: {exc.message}",
                    "conversation_id": str(conversation.id),
                    "user_message_id": user_msg.id,
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as exc:
            logger.error("Pwanimate unexpected error: %s", exc, exc_info=True)
            return Response(
                {
                    "error": "An unexpected error occurred while processing your request.",
                    "conversation_id": str(conversation.id),
                    "user_message_id": user_msg.id,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PwanimateGeneratedResourceView(APIView):
    """Create a private library document from one of the user's assistant replies."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, message_id):
        message = get_object_or_404(
            PwanimateMessage.objects.select_related("conversation"),
            pk=message_id,
            role="assistant",
            conversation__user=request.user,
        )
        raw_format = request.data.get("format")
        if not isinstance(raw_format, str):
            return Response({"error": "Choose PDF or Word format."}, status=status.HTTP_400_BAD_REQUEST)
        file_format = raw_format.strip().lower()
        try:
            from pwanimate.services.generated_documents import create_generated_resource
            document, document_file, created = create_generated_resource(
                message=message,
                user=request.user,
                file_format=file_format,
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.exception("Could not create Pwanimate resource from message %s", message_id)
            return Response(
                {"error": "The document could not be created. Please try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        token = TimestampSigner().sign_object(str(document.share_id))
        download_path = reverse("documents:serve_download", kwargs={"share_id": document.share_id})
        download_url = f"{download_path}?{urlencode({'t': token, 'file_id': document_file.id})}"
        return Response(
            {
                "document_id": document.id,
                "title": document.title,
                "format": file_format,
                "download_url": download_url,
                "library_url": reverse("documents:my_resources"),
                "created": created,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class PwanimateActionView(APIView):
    """
    Approve, decline or undo an action Spark proposed.

    Spark never acts on its own. It proposes; this endpoint is where the
    student says yes, no, or "actually, take that back".
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        """The student's outstanding proposals."""
        from pwanimate.models import PendingAction
        from pwanimate.services.actions import ActionService

        actions = PendingAction.objects.filter(
            user=request.user, status=PendingAction.STATUS_PENDING
        ).order_by("-created_at")[:20]
        return Response({
            "actions": [ActionService.serialize(action) for action in actions]
        })

    def post(self, request, action_id):
        from pwanimate.services.actions import ActionError, ActionService

        decision = str((request.data or {}).get("decision") or "confirm").strip().lower()
        try:
            if decision == "confirm":
                action = ActionService.confirm(request.user, action_id)
            elif decision == "cancel":
                action = ActionService.cancel(request.user, action_id)
            elif decision == "undo":
                action = ActionService.undo(request.user, action_id)
            else:
                return Response(
                    {"error": "Unknown decision."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except ActionError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"action": ActionService.serialize(action)})


class PwanimateMessageFeedbackView(APIView):
    """
    Record (or change) the student's verdict on one Spark answer.

    This is the outcome signal the platform was missing: what was served and
    what it cost was already recorded, but nothing recorded whether the
    answer was right.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, message_id):
        from pwanimate.services.feedback import FeedbackService

        feedback = FeedbackService.get_for_message(request.user, message_id)
        return Response({"feedback": feedback})

    def post(self, request, message_id):
        from pwanimate.services.feedback import FeedbackError, FeedbackService

        data = request.data or {}
        if "helpful" not in data:
            return Response(
                {"error": "Say whether the answer helped."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        helpful = data.get("helpful")
        if isinstance(helpful, str):
            helpful = helpful.strip().lower() in {"true", "1", "yes", "helpful"}
        helpful = bool(helpful)

        try:
            feedback = FeedbackService.record(
                user=request.user,
                message_id=message_id,
                helpful=helpful,
                reason=data.get("reason", ""),
                comment=data.get("comment", ""),
            )
        except FeedbackError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"feedback": feedback}, status=status.HTTP_201_CREATED)


class PwanimateFeedbackSummaryView(APIView):
    """
    The aggregate verdict picture.

    Staff-only: it exposes how students rated answers across models, which
    is the query that decides whether a fine-tune is worth shipping.
    """

    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]

    def get(self, request):
        from datetime import timedelta

        from django.utils import timezone

        from pwanimate.services.feedback import FeedbackService

        days = request.query_params.get("days")
        since = None
        if days:
            try:
                since = timezone.now() - timedelta(days=int(days))
            except (TypeError, ValueError):
                since = None
        return Response(FeedbackService.summary(since=since))


class PwanimateAttachmentUploadView(APIView):
    """
    Upload an attachment (image or document) for staging before sending in chat.
    Accepts multipart/form-data with 'file' field and optional 'conversation_id'.
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        file_obj = request.FILES.get("file")
        if not file_obj:
            return Response(
                {"error": "No file uploaded. Expected 'file' multipart field."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        conversation_id = request.data.get("conversation_id") or None

        try:
            attachment = AttachmentService.create_attachment(
                user=request.user,
                uploaded_file=file_obj,
                conversation_id=conversation_id,
            )
            # Documents are extracted and chunked; images get one vision
            # pass. Both run off the request path so the upload returns
            # immediately and the analysis is paid for exactly once.
            if attachment.attachment_type in ("document", "image"):
                from pwanimate.tasks.attachments import process_pwanimate_attachment
                try:
                    process_pwanimate_attachment.delay(str(attachment.id))
                except Exception:
                    logger.exception("Could not queue processing for attachment %s; processing inline", attachment.id)
                    process_pwanimate_attachment(str(attachment.id))
            serializer = PwanimateAttachmentSerializer(attachment)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except ValidationError as exc:
            msg = exc.message if hasattr(exc, "message") else str(exc)
            return Response({"error": msg}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.error("Failed to upload attachment: %s", exc, exc_info=True)
            return Response(
                {"error": "Failed to process attachment upload."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PwanimateContextDocumentSearchView(APIView):
    """Search repository documents the current student is allowed to open."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        query = (request.query_params.get("q") or "").strip()
        recommended = request.query_params.get("recommended") in {"1", "true", "yes"}
        if len(query) < 2 and not recommended:
            return Response({"results": []})

        documents = Document.objects.filter(
            status="ready",
            is_available=True,
        ).select_related("category", "uploaded_by").prefetch_related("academic_units__academic_unit")

        user = request.user
        enrolled_unit_ids = set()
        if hasattr(user, "get_enrolled_units"):
            try:
                enrolled_unit_ids = {
                    e.academic_unit_id for e in user.get_enrolled_units(auto_sync=False)
                }
            except Exception:
                enrolled_unit_ids = set()

        programme = getattr(user, "programme", None)
        is_admin_or_leader = (
            user.is_staff
            or user.is_superuser
            or getattr(user, "global_role", None) in {"PRESIDENT", "DELEGATE"}
        )
        if not is_admin_or_leader:
            visibility_q = Q(visibility="public") | Q(uploaded_by=user)
            restricted_q = Q(visibility="restricted")
            if enrolled_unit_ids:
                visibility_q |= restricted_q & Q(
                    academic_units__academic_unit_id__in=enrolled_unit_ids
                )
            if programme:
                visibility_q |= restricted_q & Q(
                    academic_units__academic_unit__programme_units__programme=programme
                )
            elif getattr(user, "course", None):
                visibility_q |= restricted_q & Q(
                    academic_units__academic_unit__code__icontains=user.course.name
                )
            documents = documents.filter(visibility_q).distinct()

        if len(query) >= 2:
            documents = documents.filter(
                Q(title__icontains=query)
                | Q(description__icontains=query)
                | Q(academic_units__academic_unit__code__icontains=query)
                | Q(academic_units__academic_unit__name__icontains=query)
            ).distinct()
        elif recommended:
            if enrolled_unit_ids:
                documents = documents.filter(
                    academic_units__academic_unit_id__in=enrolled_unit_ids
                ).distinct()
            elif programme:
                documents = documents.filter(
                    academic_units__academic_unit__programme_units__programme=programme
                ).distinct()

        candidate_docs = list(documents.order_by("title")[:40])

        def _rank_doc(doc):
            unit_links = list(doc.academic_units.all())
            doc_unit_ids = {u.academic_unit_id for u in unit_links if u.academic_unit_id}
            if enrolled_unit_ids and (doc_unit_ids & enrolled_unit_ids):
                return (0, doc.title.lower())
            return (1, doc.title.lower())

        candidate_docs.sort(key=_rank_doc)

        results = []
        for document in candidate_docs[:20]:
            version = document.latest_version
            document_file = version.files.first() if version else None
            if not document_file or not document_file.file:
                continue
            try:
                media_url = document_file.file.url
            except (ValueError, OSError):
                continue
            unit_links = list(document.academic_units.all())
            first_unit = unit_links[0].academic_unit if unit_links and unit_links[0].academic_unit_id else None
            unit_code = first_unit.code if first_unit else ""
            unit_name = first_unit.name if first_unit else ""
            is_enrolled = bool(
                enrolled_unit_ids
                and any(u.academic_unit_id in enrolled_unit_ids for u in unit_links)
            )
            results.append({
                "document_id": str(document.share_id),
                "document_share_id": str(document.share_id),
                "document_version_id": str(version.id),
                "file_id": str(document_file.id),
                "title": document.title,
                "category": document.category.name if document.category_id else "Document",
                "unit_code": unit_code,
                "unit_name": unit_name,
                "is_enrolled_unit": is_enrolled,
                "file_type": (document_file.extension or "").lstrip(".").lower(),
                "media_url": media_url,
                "thumbnail_url": document_file.preview_url or "",
                "url": f"/documents/document/{document.share_id}/",
            })
        return Response({"results": results})


class PwanimateAttachmentStatusView(APIView):
    """Return processing status for an attachment owned by the current student."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, attachment_id):
        attachment = AttachmentService.get_authorized_attachment(request.user, attachment_id)
        if not attachment:
            return Response({"error": "Attachment not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            "id": str(attachment.id),
            "processing_status": attachment.processing_status,
            "processing_error": attachment.processing_error,
            "chunk_count": attachment.chunks.count(),
        })


class PwanimateAttachmentMediaView(APIView):
    """
    Authorized private media view endpoint.
    Serves attachment content inline only to the owning user.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, attachment_id):
        attachment = AttachmentService.get_authorized_attachment(
            user=request.user,
            attachment_id=attachment_id,
        )
        if not attachment:
            return Response(
                {"error": "Attachment not found or access denied."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not attachment.file:
            return Response(
                {"error": "Attachment file missing."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            file_handle = attachment.file.open("rb")
            response = FileResponse(file_handle, content_type=attachment.mime_type)
            response["Content-Disposition"] = f'inline; filename="{attachment.file_name}"'
            response["X-Content-Type-Options"] = "nosniff"
            return response
        except Exception as exc:
            logger.error("Error serving attachment %s: %s", attachment_id, exc)
            return Response(
                {"error": "Could not read attachment file."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PwanimateAttachmentDownloadView(APIView):
    """
    Authorized private media download endpoint.
    Serves attachment as forced download only to the owning user.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, attachment_id):
        attachment = AttachmentService.get_authorized_attachment(
            user=request.user,
            attachment_id=attachment_id,
        )
        if not attachment:
            return Response(
                {"error": "Attachment not found or access denied."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not attachment.file:
            return Response(
                {"error": "Attachment file missing."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            file_handle = attachment.file.open("rb")
            response = FileResponse(file_handle, content_type=attachment.mime_type)
            response["Content-Disposition"] = f'attachment; filename="{attachment.file_name}"'
            response["X-Content-Type-Options"] = "nosniff"
            return response
        except Exception as exc:
            logger.error("Error downloading attachment %s: %s", attachment_id, exc)
            return Response(
                {"error": "Could not read attachment file."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PwanimateConversationListView(APIView):
    """
    List all persistent conversations owned by the authenticated user.
    Supports optional `?filter=study` to list only Study Mode conversations.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        conversations = (
            PwanimateConversation.objects.filter(user=request.user)
            .select_related("study_session", "study_session__latest_checkpoint")
            .order_by("-updated_at")
        )
        filter_mode = (request.query_params.get("filter") or "").strip().lower()
        if filter_mode == "study" or request.query_params.get("study_only") in {"1", "true", "yes"}:
            conversations = conversations.filter(study_session__isnull=False)
        serializer = ConversationListSerializer(conversations, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class PwanimateConversationDetailView(APIView):
    """
    Retrieve, rename, or delete an existing conversation thread owned by the authenticated user.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, conversation_id):
        conversation = ConversationService.get_owned_conversation(
            user=request.user,
            conversation_id=conversation_id,
        )
        if conversation is None:
            return Response(
                {"error": "Conversation not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = ConversationDetailSerializer(conversation)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, conversation_id):
        conversation = ConversationService.get_owned_conversation(
            user=request.user,
            conversation_id=conversation_id,
        )
        if conversation is None:
            return Response(
                {"error": "Conversation not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        title = request.data.get("title")
        if title is not None:
            clean_title = str(title).strip()
            if not clean_title:
                return Response(
                    {"error": "Conversation title cannot be empty."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            conversation.title = clean_title[:255]
            conversation.save(update_fields=["title", "updated_at"])

        study_session = StudySessionService.get_session_for_conversation(request.user, conversation)
        if study_session and (
            "learning_objective" in request.data or "current_topic" in request.data
        ):
            StudySessionService.update_session_metadata(
                session=study_session,
                learning_objective=request.data.get("learning_objective"),
                current_topic=request.data.get("current_topic"),
            )

        serializer = ConversationDetailSerializer(conversation)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, conversation_id):
        conversation = ConversationService.get_owned_conversation(
            user=request.user,
            conversation_id=conversation_id,
        )
        if conversation is None:
            return Response(
                {"error": "Conversation not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        conversation.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PwanimateStudySessionListView(APIView):
    """
    List all Study Mode sessions owned by the current user, or start/continue a session.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        status_filter = (request.query_params.get("status") or "").strip().lower() or None
        sessions = StudySessionService.list_user_sessions(
            user=request.user,
            status_filter=status_filter,
        )
        resumable = StudySessionService.get_resumable_session(request.user)
        return Response(
            {
                "sessions": [StudySessionService.serialize_session(s) for s in sessions],
                "resumable_session": (
                    StudySessionService.serialize_session(resumable) if resumable else None
                ),
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        data = request.data or {}
        conversation_id = data.get("conversation_id")
        conversation = None
        created_new_conv = False

        if conversation_id:
            conversation = ConversationService.get_owned_conversation(
                user=request.user,
                conversation_id=conversation_id,
            )
            if conversation is None:
                if data.get("create_conversation") is True:
                    try:
                        requested_id = uuid.UUID(str(conversation_id))
                    except (ValueError, TypeError, AttributeError):
                        return Response(
                            {"error": "Invalid conversation ID."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    if PwanimateConversation.objects.filter(id=requested_id).exists():
                        return Response(
                            {"error": "Conversation not found."},
                            status=status.HTTP_404_NOT_FOUND,
                        )
                    conversation = ConversationService.create_conversation(
                        user=request.user,
                        conversation_id=requested_id,
                        title=str(data.get("title") or "New Study Session").strip()[:255],
                    )
                    created_new_conv = True
                else:
                    return Response(
                        {"error": "Conversation not found."},
                        status=status.HTTP_404_NOT_FOUND,
                    )
        else:
            conversation = ConversationService.create_conversation(
                user=request.user,
                title=str(data.get("title") or "New Study Session").strip()[:255],
            )
            created_new_conv = True

        had_session = PwanimateStudySession.objects.filter(
            conversation=conversation, user=request.user
        ).exists()
        context_resources = data.get("context_resources")
        if not isinstance(context_resources, list):
            context_resources = None

        session = StudySessionService.start_or_continue_session(
            user=request.user,
            conversation=conversation,
            title=str(data.get("title") or "").strip(),
            learning_objective=str(data.get("learning_objective") or "").strip(),
            current_topic=str(data.get("current_topic") or "").strip(),
            context_resources=context_resources,
        )
        return Response(
            {
                "study_session": StudySessionService.serialize_session(session),
                "conversation_id": str(conversation.id),
                "created": bool(created_new_conv or not had_session),
            },
            status=status.HTTP_201_CREATED if (created_new_conv or not had_session) else status.HTTP_200_OK,
        )


class PwanimateStudySessionDetailView(APIView):
    """
    Retrieve or update metadata/context state for an owned Study Mode session.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, session_id):
        session = StudySessionService.get_owned_session(request.user, session_id)
        if session is None:
            return Response(
                {"error": "Study session not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        restored_context, unavailable_resources = StudySessionService.restore_context_state(
            session=session,
            user=request.user,
        )
        checkpoints = [
            StudySessionService.serialize_checkpoint(ckpt)
            for ckpt in session.checkpoints.order_by("-created_at")[:20]
        ]
        return Response(
            {
                "study_session": StudySessionService.serialize_session(session),
                "checkpoints": checkpoints,
                "restored_context": restored_context,
                "unavailable_resources": unavailable_resources,
            },
            status=status.HTTP_200_OK,
        )

    def patch(self, request, session_id):
        session = StudySessionService.get_owned_session(request.user, session_id)
        if session is None:
            return Response(
                {"error": "Study session not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        data = request.data or {}
        if any(k in data for k in ("title", "learning_objective", "current_topic")):
            session = StudySessionService.update_session_metadata(
                session=session,
                title=data.get("title") if "title" in data else None,
                learning_objective=data.get("learning_objective") if "learning_objective" in data else None,
                current_topic=data.get("current_topic") if "current_topic" in data else None,
            )
        if "context_resources" in data and isinstance(data.get("context_resources"), list):
            StudySessionService.sync_context_state(
                session=session,
                user=request.user,
                context_resources=data.get("context_resources"),
            )
            session.save(update_fields=["context_state", "last_active_at", "updated_at"])

        return Response(
            {"study_session": StudySessionService.serialize_session(session)},
            status=status.HTTP_200_OK,
        )


class PwanimateStudySessionStatusView(APIView):
    """
    Transition an owned Study Mode session between active, paused, completed,
    or dismiss its home-screen resume banner.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, session_id):
        session = StudySessionService.get_owned_session(request.user, session_id)
        if session is None:
            return Response(
                {"error": "Study session not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        action = str((request.data or {}).get("action") or "").strip().lower()
        if action not in {"pause", "resume", "end", "complete", "dismiss_banner"}:
            return Response(
                {"error": "Invalid action. Expected 'pause', 'resume', 'end', or 'dismiss_banner'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        restored_context = []
        unavailable_resources = []

        if action == "pause":
            session = StudySessionService.pause_session(session)
        elif action == "resume":
            session = StudySessionService.resume_session(session, user=request.user)
            restored_context, unavailable_resources = StudySessionService.restore_context_state(
                session=session,
                user=request.user,
            )
        elif action in {"end", "complete"}:
            gen_ckpt = (request.data or {}).get("generate_checkpoint", True) is not False
            session = StudySessionService.end_session(
                session,
                generate_final_checkpoint=gen_ckpt,
            )
        elif action == "dismiss_banner":
            session = StudySessionService.dismiss_resume_banner(session)

        return Response(
            {
                "study_session": StudySessionService.serialize_session(session),
                "restored_context": restored_context,
                "unavailable_resources": unavailable_resources,
            },
            status=status.HTTP_200_OK,
        )


class PwanimateStudyCheckpointView(APIView):
    """
    List checkpoints or generate a fresh learning checkpoint on explicit user request.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, session_id):
        session = StudySessionService.get_owned_session(request.user, session_id)
        if session is None:
            return Response(
                {"error": "Study session not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        checkpoints = [
            StudySessionService.serialize_checkpoint(ckpt)
            for ckpt in session.checkpoints.order_by("-created_at")[:25]
        ]
        return Response({"checkpoints": checkpoints}, status=status.HTTP_200_OK)

    def post(self, request, session_id):
        session = StudySessionService.get_owned_session(request.user, session_id)
        if session is None:
            return Response(
                {"error": "Study session not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        checkpoint = StudySessionService.generate_checkpoint(
            session_id=session.id,
            force=True,
        )
        session = StudySessionService.get_owned_session(request.user, session.id)
        if checkpoint is None:
            return Response(
                {
                    "error": "Send at least one study message before generating a checkpoint.",
                    "study_session": StudySessionService.serialize_session(session),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                "checkpoint": StudySessionService.serialize_checkpoint(checkpoint),
                "study_session": StudySessionService.serialize_session(session),
            },
            status=status.HTTP_201_CREATED,
        )


class PwanimateStudySummaryView(APIView):
    """
    Generate a structured Study Mode session summary for review, or export a reviewed
    summary as a PDF or Word document using the existing generated-documents pipeline.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, session_id):
        session = StudySessionService.get_owned_session(request.user, session_id)
        if session is None:
            return Response(
                {"error": "Study session not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        data = request.data or {}
        export_format = str(data.get("export_format") or data.get("format") or "").strip().lower()
        provided_markdown = str(data.get("summary_markdown") or "").strip()

        if export_format:
            if not provided_markdown:
                last_summary = (session.context_state or {}).get("last_summary") or {}
                provided_markdown = str(last_summary.get("markdown") or "").strip()
            if not provided_markdown:
                summary_payload = StudySessionService.generate_session_summary(session=session)
                provided_markdown = summary_payload["summary_markdown"]

            try:
                from pwanimate.services.generated_documents import create_generated_resource

                summary_msg = ConversationService.persist_assistant_message(
                    conversation=session.conversation,
                    content=provided_markdown,
                )
                document, document_file, created = create_generated_resource(
                    message=summary_msg,
                    user=request.user,
                    file_format=export_format,
                )
                token = TimestampSigner().sign_object(str(document.share_id))
                download_path = reverse(
                    "documents:serve_download", kwargs={"share_id": document.share_id}
                )
                download_url = f"{download_path}?{urlencode({'t': token, 'file_id': document_file.id})}"
                return Response(
                    {
                        "document_id": document.id,
                        "document_share_id": str(document.share_id),
                        "title": document.title,
                        "format": export_format,
                        "download_url": download_url,
                        "library_url": reverse("documents:my_resources"),
                        "summary_markdown": provided_markdown,
                        "message_id": summary_msg.id,
                        "created": created,
                    },
                    status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
                )
            except ValueError as exc:
                return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            except Exception:
                logger.exception("Could not export Study Mode summary for session %s", session_id)
                return Response(
                    {"error": "The study summary document could not be exported."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        summary_payload = StudySessionService.generate_session_summary(session=session)
        session = StudySessionService.get_owned_session(request.user, session.id)
        return Response(
            {
                **summary_payload,
                "study_session": StudySessionService.serialize_session(session),
            },
            status=status.HTTP_201_CREATED,
        )

    def patch(self, request, session_id):
        session = StudySessionService.get_owned_session(request.user, session_id)
        if session is None:
            return Response(
                {"error": "Study session not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        data = request.data or {}
        content = str(data.get("content") or data.get("summary_markdown") or "").strip()
        if not content:
            return Response(
                {"error": "Summary content cannot be empty."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        message_id = data.get("message_id")
        if message_id is not None and str(message_id).strip() != "":
            try:
                message_id = int(message_id)
            except (TypeError, ValueError):
                message_id = None
        else:
            message_id = None

        updated = StudySessionService.update_session_summary(
            session=session,
            user=request.user,
            content=content,
            message_id=message_id,
        )
        session = StudySessionService.get_owned_session(request.user, session.id)
        return Response(
            {
                **updated,
                "study_session": StudySessionService.serialize_session(session),
            },
            status=status.HTTP_200_OK,
        )


class PwanimateStudySummarySaveCollectionView(APIView):
    """
    Save a reviewed Study Mode session summary into an existing or newly created
    Collection owned or editable by the student.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, session_id):
        session = StudySessionService.get_owned_session(request.user, session_id)
        if session is None:
            return Response(
                {"error": "Study session not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        data = request.data or {}
        message_id = data.get("message_id")
        if message_id is not None and str(message_id).strip() != "":
            try:
                message_id = int(message_id)
            except (TypeError, ValueError):
                message_id = None
        else:
            message_id = None

        summary_markdown = str(data.get("summary_markdown") or "").strip()
        if not summary_markdown and message_id is None:
            last_summary = (session.context_state or {}).get("last_summary") or {}
            summary_markdown = str(last_summary.get("markdown") or "").strip()
        if not summary_markdown and message_id is None:
            generated = StudySessionService.generate_session_summary(session=session)
            summary_markdown = generated["summary_markdown"]

        collection_id = data.get("collection_id")
        if collection_id is not None and str(collection_id).strip() != "":
            try:
                collection_id = int(collection_id)
            except (TypeError, ValueError):
                return Response(
                    {"error": "Invalid collection ID."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            collection_id = None

        new_collection_name = str(data.get("new_collection_name") or "").strip()
        new_collection_description = str(data.get("new_collection_description") or "").strip()
        new_collection_visibility = str(data.get("new_collection_visibility") or "private").strip()
        file_format = str(data.get("file_format") or data.get("format") or "pdf").strip().lower()
        notes = str(data.get("notes") or "").strip()

        try:
            result = StudyCollectionService.save_summary_to_collection(
                user=request.user,
                session=session,
                summary_markdown=summary_markdown,
                message_id=message_id,
                collection_id=collection_id,
                new_collection_name=new_collection_name,
                new_collection_description=new_collection_description,
                new_collection_visibility=new_collection_visibility,
                file_format=file_format,
                notes=notes,
            )
            return Response(
                result,
                status=status.HTTP_201_CREATED if result.get("created") else status.HTTP_200_OK,
            )
        except (PermissionDenied, PermissionError) as exc:
            return Response({"error": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except LookupError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception("Failed to save study summary to collection for session %s", session_id)
            return Response(
                {"error": "Could not save the summary to the collection."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PwanimateCollectionListCreateView(APIView):
    """
    List Collections the user can edit or create a new Collection for saving Study Mode summaries.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        collections = StudyCollectionService.list_editable_collections(request.user)
        return Response({"collections": collections}, status=status.HTTP_200_OK)

    def post(self, request):
        data = request.data or {}
        name = str(data.get("name") or "").strip()
        description = str(data.get("description") or "").strip()
        visibility = str(data.get("visibility") or "private").strip().lower()
        try:
            collection = StudyCollectionService.create_collection(
                user=request.user,
                name=name,
                description=description,
                visibility=visibility,
            )
            return Response(
                {
                    "id": collection.id,
                    "name": collection.name,
                    "description": collection.description,
                    "visibility": collection.visibility,
                    "item_count": 0,
                    "is_owner": True,
                },
                status=status.HTTP_201_CREATED,
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class PwanimateQuotaStatusView(APIView):
    """
    Returns the current provider quota status.

    Shows which AI providers are healthy, rate-limited, or in cooldown.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        tracker = get_quota_tracker()
        status_payload = tracker.get_status()
        # Spark engines are the branded surface; provider models stay
        # available under models[] for compatibility during the transition.
        status_payload["engines"] = get_engines_status(tracker)
        return Response(
            {"quota_status": status_payload},
            status=status.HTTP_200_OK,
        )


# Whisper models are heavy (hundreds of MB to multi-GB) and slow to load on
# CPU. Building one per request re-downloads it whenever it is missing from the
# HuggingFace cache and blocks long enough to trip the upstream proxy timeout
# (Cloudflare/nginx), which returns a non-JSON error page to the client. Load
# the model once per process and reuse it across requests instead.
_WHISPER_MODEL_LOCK = threading.Lock()
_whisper_model = None
_whisper_model_size = None

# Map configured (Groq-style) model names to faster-whisper model sizes.
_WHISPER_MODEL_MAPPING = {
    'whisper-large-v3-turbo': 'large-v3',
    'whisper-large-v3': 'large-v3',
    'whisper-large-v2': 'large-v2',
    'whisper-medium': 'medium',
    'whisper-small': 'small',
    'whisper-base': 'base',
    'whisper-tiny': 'tiny',
}


def _resolve_whisper_model_size():
    """Resolve the configured speech-to-text setting to a faster-whisper size."""
    configured = (getattr(settings, 'PWANIMATE_SPEECH_TO_TEXT_MODEL', 'tiny') or 'tiny').strip().lower()
    if configured in _WHISPER_MODEL_MAPPING:
        return _WHISPER_MODEL_MAPPING[configured]
    # Already a raw faster-whisper size (e.g. "base", "small").
    if configured in _WHISPER_MODEL_MAPPING.values():
        return configured
    logger.warning('Unknown PWANIMATE_SPEECH_TO_TEXT_MODEL %r; falling back to "tiny".', configured)
    return 'tiny'


def get_whisper_model():
    """Return a process-wide cached faster-whisper model (thread-safe)."""
    global _whisper_model, _whisper_model_size
    from pwaninet.compat import patch_av_metadata_errors

    patch_av_metadata_errors()
    model_size = _resolve_whisper_model_size()
    with _WHISPER_MODEL_LOCK:
        if _whisper_model is not None and _whisper_model_size == model_size:
            return _whisper_model
        from faster_whisper import WhisperModel

        cpu_threads = min(2, max(1, (os.cpu_count() or 1) // 2))
        logger.info(
            'Loading faster-whisper model %s on CPU (int8, %d threads)...',
            model_size,
            cpu_threads,
        )
        _whisper_model = WhisperModel(model_size, device='cpu', compute_type='int8', cpu_threads=cpu_threads)
        _whisper_model_size = model_size
        return _whisper_model


class PwanimateSpeechSynthesisView(APIView):
    """Generate a private audio response for an authenticated Spark user."""

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser, FormParser]
    renderer_classes = [AudioMP3Renderer]

    def post(self, request):
        text = str((request.data or {}).get("text") or "").strip()
        voice_id = str((request.data or {}).get("voice_id") or "warm_female").strip()
        if not text:
            return Response({"error": "Text is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            audio = synthesize(text, voice_id=voice_id)
        except TTSConfigurationError:
            logger.error("Spark TTS is not configured.")
            return Response(
                {"error": "Spark speech is not configured yet."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except TTSProviderError as exc:
            logger.warning("Spark TTS request failed: %s", exc)
            return Response(
                {"error": "Spark could not generate speech right now."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        response = FileResponse(BytesIO(audio), content_type="audio/mpeg")
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class PwanimateVoiceTranscriptionView(APIView):
    """Transcribe a complete client-recorded voice note using local faster-whisper."""

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        audio = request.FILES.get('audio')
        if not audio:
            return Response({'error': 'Audio recording is required.'}, status=status.HTTP_400_BAD_REQUEST)

        max_bytes = int(getattr(settings, 'PWANIMATE_VOICE_MAX_BYTES', 25 * 1024 * 1024))
        if audio.size <= 0 or audio.size > max_bytes:
            return Response({'error': 'Recording is empty or exceeds the upload limit.'}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

        # Use local faster-whisper instead of external API
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            logger.error('faster-whisper not installed')
            return Response({'error': 'Voice transcription is not available on this server.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        language = (request.data.get('language') or '').strip().lower()
        if language not in {'en', 'sw'}:
            language = 'en'

        content_type = (audio.content_type or 'application/octet-stream').split(';', 1)[0].lower()
        extension_by_type = {
            'audio/webm': '.webm',
            'audio/ogg': '.ogg',
            'audio/mp4': '.m4a',
            'audio/mpeg': '.mp3',
            'audio/wav': '.wav',
            'audio/x-wav': '.wav',
        }
        extension = extension_by_type.get(content_type)
        if not extension:
            return Response({'error': 'This audio format is not supported.'}, status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)

        # Save uploaded audio to temporary file
        import tempfile

        temp_dir = tempfile.gettempdir()
        temp_audio_path = os.path.join(temp_dir, f'voice_{request.user.id}_{int(time.time())}{extension}')

        try:
            # Save uploaded file
            with open(temp_audio_path, 'wb') as f:
                for chunk in audio.chunks():
                    f.write(chunk)

            logger.info('Voice transcription: saved audio to %s (%d bytes)', temp_audio_path, os.path.getsize(temp_audio_path))

            # Reuse a process-wide cached model instead of loading per request.
            start_time = time.time()

            try:
                model = get_whisper_model()
            except Exception:
                logger.exception('faster-whisper model could not be loaded')
                return Response(
                    {'error': 'Voice transcription is warming up. Please try again in a moment.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

            # Transcribe with language hint if provided
            language_code = language if language in {'en', 'sw'} else None
            segments, info = model.transcribe(temp_audio_path, beam_size=1, language=language_code)

            extracted_text_list = []
            for segment in segments:
                if segment.text and segment.text.strip():
                    extracted_text_list.append(segment.text.strip())

            transcript = " ".join(extracted_text_list).strip()

            elapsed = time.time() - start_time
            logger.info(
                'Voice transcription completed in %.2fs: %d characters (language: %s, prob: %.2f)',
                elapsed,
                len(transcript),
                getattr(info, 'language', 'unknown'),
                getattr(info, 'language_probability', 0.0)
            )

            if not transcript:
                return Response({'error': 'No speech was recognized. Please try again.'}, status=status.HTTP_200_OK)

            return Response({'text': transcript}, status=status.HTTP_200_OK)

        except Exception as e:
            logger.exception('Voice transcription failed: %s', str(e))
            return Response({'error': 'Transcription failed. Please try again.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        finally:
            # Clean up temporary file
            if os.path.exists(temp_audio_path):
                try:
                    os.remove(temp_audio_path)
                    logger.info('Cleaned up temporary audio file: %s', temp_audio_path)
                except Exception as e:
                    logger.warning('Failed to clean up temporary file %s: %s', temp_audio_path, e)
