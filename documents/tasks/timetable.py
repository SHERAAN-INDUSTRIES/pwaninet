"""
Celery background task: AI-powered timetable image processing.

Separates the slow vision extraction call from the upload request so the
student gets an instant response while AI extracts the schedule.

Queue: docs_queue (shared with document processing pipeline)
"""

import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    queue='docs_queue',
    name='documents.tasks.timetable.process_timetable_image_task',
)
def process_timetable_image_task(self, timetable_id: int) -> dict:
    """
    Background task that:
      1. Reads the saved timetable image from storage.
      2. Calls the configured vision provider to extract the weekly schedule JSON.
      3. Computes free study slots.
      4. Marks the timetable ACTIVE and saves schedule_data.

    On failure the task retries up to 3 times (30-second back-off);
    on final failure the record is marked FAILED so the UI can surface
    a meaningful error state instead of hanging at "Processing".
    """
    from documents.academic.models import ProgrammeTimetable, TimetableStatus
    from documents.academic.timetable_service import TimetableExtractionService, compute_free_slots, validate_schedule_data

    # ── 1. Fetch record ───────────────────────────────────────────────────────
    try:
        timetable = ProgrammeTimetable.objects.select_related(
            'programme', 'academic_level', 'institution'
        ).get(pk=timetable_id)
    except ProgrammeTimetable.DoesNotExist:
        logger.error(
            '[TIMETABLE-TASK] ProgrammeTimetable(id=%s) not found — aborting.', timetable_id
        )
        return {'success': False, 'error': 'Timetable record not found.'}

    # Guard: if somehow already active (e.g. duplicate task delivery), skip.
    if timetable.status == TimetableStatus.ACTIVE and timetable.is_active:
        logger.info('[TIMETABLE-TASK] Timetable(id=%s) already ACTIVE — skipping.', timetable_id)
        return {'success': True, 'skipped': True}

    # ── 2. Mark PROCESSING ────────────────────────────────────────────────────
    timetable.status = TimetableStatus.PROCESSING
    timetable.save(update_fields=['status', 'updated_at'])

    # ── 3. Read image bytes from storage ─────────────────────────────────────
    try:
        image_field = timetable.source_image
        if not image_field or not image_field.name:
            raise ValueError('No source image on this timetable record.')

        # Determine MIME type from extension
        name_lower = image_field.name.lower()
        if name_lower.endswith('.png'):
            mime_type = 'image/png'
        elif name_lower.endswith('.webp'):
            mime_type = 'image/webp'
        else:
            mime_type = 'image/jpeg'  # default for .jpg / .jpeg

        # Read bytes — works for both local FileSystemStorage and remote backends (S3/R2)
        image_field.seek(0)
        image_bytes = image_field.read()

    except Exception as exc:
        logger.error(
            '[TIMETABLE-TASK] Cannot read image for timetable(id=%s): %s', timetable_id, exc
        )
        _mark_failed(timetable)
        raise self.retry(exc=exc)

    # ── 4. Call configured vision provider ────────────────────────────────────
    try:
        programme_name = timetable.programme.name if timetable.programme else ''
        level_name = timetable.academic_level.name if timetable.academic_level else ''
        institution_name = timetable.institution.name if timetable.institution else ''
        chain = TimetableExtractionService._resolve_ocr_chain()
        chain_label = ' -> '.join(
            f"{c.get('provider')}:{c.get('model')}" for c in chain
        ) or 'none'

        logger.info(
            '[TIMETABLE-TASK] Starting vision extraction (chain=%s) for timetable(id=%s) — %s %s.',
            chain_label, timetable_id, programme_name, level_name,
        )

        schedule_data = TimetableExtractionService.extract_from_image(
            image_bytes=image_bytes,
            mime_type=mime_type,
            programme_name=programme_name,
            level_name=level_name,
            institution_name=institution_name,
        )

        if not schedule_data or not isinstance(schedule_data, dict):
            raise ValueError('extract_from_image returned empty or non-dict result.')

        schedule_data = validate_schedule_data(schedule_data)

        # Ensure free_slots are present (extract_from_image already computes them,
        # but be defensive in case a fallback path was taken).
        if 'free_slots' not in schedule_data:
            schedule_data['free_slots'] = compute_free_slots(
                schedule_data.get('weekly_schedule', [])
            )

    except Exception as exc:
        logger.error(
            '[TIMETABLE-TASK] Extraction failed for timetable(id=%s): %s',
            timetable_id, exc, exc_info=True,
        )
        _mark_failed(timetable)
        raise self.retry(exc=exc)

    # ── 5. Persist extracted schedule and activate ────────────────────────────
    from documents.academic.timetable_service import ProgrammeTimetableManager
    ProgrammeTimetableManager.activate_timetable(timetable, schedule_data)

    slot_count = len(schedule_data.get('weekly_schedule', []))
    free_count = len(schedule_data.get('free_slots', []))
    logger.info(
        '[TIMETABLE-TASK] ✓ Timetable(id=%s) activated — %d lecture slots, %d free slots.',
        timetable_id, slot_count, free_count,
    )

    # Notify the uploader — processing is fully background, so this push/in-app
    # notification is how they learn the timetable is ready.
    try:
        from notifications.events import publish_event, EventTypes, EventSources, EventActions
        publish_event(
            event_type=EventTypes.DOCUMENTS_TIMETABLE_READY.value,
            source=EventSources.DOCUMENTS.value,
            action=EventActions.COMPLETED.value,
            actor=timetable.uploaded_by,
            target_type='ProgrammeTimetable',
            target_id=str(timetable_id),
            metadata={
                'timetable_title': timetable.title,
                'slot_count': slot_count,
            },
        )
    except Exception as notif_exc:
        logger.warning('[TIMETABLE-TASK] Failed to publish ready notification: %s', notif_exc)

    return {
        'success': True,
        'timetable_id': timetable_id,
        'slot_count': slot_count,
        'free_slot_count': free_count,
    }


def _mark_failed(timetable) -> None:
    """Set timetable status to FAILED and persist without raising."""
    try:
        from documents.academic.models import TimetableStatus
        timetable.status = TimetableStatus.FAILED
        timetable.is_active = False
        timetable.save(update_fields=['status', 'is_active', 'updated_at'])
    except Exception as inner:
        logger.error('[TIMETABLE-TASK] Could not mark timetable as FAILED: %s', inner)
