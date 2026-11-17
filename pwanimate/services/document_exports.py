"""Owned file publication for the existing chat/run finalization paths."""
import re
from urllib.parse import urlencode
from django.core.signing import TimestampSigner
from django.urls import reverse
from pwanimate.ai.completion import is_complete
from pwanimate.services.export_formats import FORMATS

_FORMAT = r'pdf|docx|doc|pptx|word|powerpoint'
_NEGATIVE = re.compile(r"^\s*(?:do not|don't|how (?:do|can|to))\b|\bwithout (?:generating|creating)\b", re.I)


def requested_formats(query):
    text = str(query or '')[:8000]
    if _NEGATIVE.search(text) or '```' in text:
        return []
    text = re.sub(r'"[^"]*"|`[^`]*`', '', text)
    text = re.sub(r'\bWord 97(?:-2003)?\b', 'DOC', text, flags=re.I)
    # Recognize output instructions, not incidental "my Java PDF" input.
    cues = [
        rf'\b(?:as|in|into|to)\s+(?:(?:a|an|the)\s+)?\.?(?:{_FORMAT})\b',
        rf'\b(?:generate|create|export|download|save|make|produce|prepare|give me|send me|provide)\s+(?:me\s+)?(?:(?:a|an|the)\s+)?(?:{_FORMAT})\b',
        rf'\b(?:{_FORMAT})\s+(?:file|document|version|presentation|slides|deck)\b',
    ]
    matches = []
    for cue in cues:
        for match in re.finditer(cue, text, re.I):
            # Include following format list (PDF and DOCX), not unrelated input.
            tail = text[match.end():]
            extra = re.match(rf'(?:\s*(?:,|/|and|or|&)\s*\.?({_FORMAT})\b)*', tail, re.I)
            matches.append((match.start(), text[match.start():match.end() + (extra.end() if extra else 0)]))
    values = []
    for _, phrase in sorted(matches):
        for name in re.findall(rf'\b({_FORMAT})\b', phrase, re.I):
            fmt = {'word': 'docx', 'powerpoint': 'pptx'}.get(name.lower(), name.lower())
            if fmt not in values: values.append(fmt)
    if not values and re.search(r'\b(?:generate|create|make|prepare|produce)\s+(?:me\s+)?(?:a\s+)?(?:document|doc|revision pack|revision notes|presentation|slide deck|slides)\b', text, re.I):
        values = ['pptx' if re.search(r'\b(?:presentation|slides|slide deck)\b', text, re.I) else 'pdf']
    return values


def resource_payload(document, resource_file):
    token = TimestampSigner().sign_object(str(document.share_id))
    path = reverse('documents:serve_download', kwargs={'share_id': document.share_id})
    return {'document_id': document.pk, 'title': document.title,
        'format': document.generated_format, 'size_bytes': resource_file.size_bytes,
        'download_url': path + '?' + urlencode({'t': token, 'file_id': resource_file.pk}),
        'source_message_id': document.generated_from_message_id}


def export_states(user, message_ids):
    from pwanimate.models import SparkAgentRun
    return {mid: {'status': status, **(state or {})} for mid, status, state in
        SparkAgentRun.objects.filter(user=user, result_message_id__in=message_ids)
        .values_list('result_message_id', 'status', 'state__document_exports') if state}


def saved_resources(user, message_ids):
    """Fresh owner-filtered signed links; URLs are never persisted in run state."""
    from documents.documents.models import Document
    from django.db.models import Q
    states = export_states(user, message_ids)
    extra = {pk for state in states.values() for pk in state.get('document_ids', [])}
    query = Q(generated_from_message_id__in=message_ids)
    if extra: query |= Q(pk__in=extra)
    result = {}
    documents = Document.objects.filter(query, uploaded_by=user, is_ai_generated=True).prefetch_related('versions__files')
    for document in documents:
        version = next((v for v in document.versions.all() if v.is_latest), None)
        file = next(iter(version.files.all()), None) if version else None
        if not file or not file.storage_private: continue
        mids = {document.generated_from_message_id} & set(message_ids)
        mids.update(mid for mid, state in states.items() if document.pk in state.get('document_ids', []))
        for mid in mids:
            if states.get(mid, {}).get('status') == 'cancelled': continue
            result.setdefault(mid, []).append(resource_payload(document, file))
    return result


def saved_export_errors(user, message_ids, resources):
    return {mid: [err for err in state.get('errors', []) if err.get('format') not in
        {f['format'] for f in resources.get(mid, [])}]
        for mid, state in export_states(user, message_ids).items() if state.get('status') != 'cancelled'}


def publish_requested_documents(request, response, message, *, on_progress=None, cancel_check=None):
    """Only completed, persisted assistant content can become a new artifact.

    This is a finalization hook using the registered tool, not another planner.
    Failed formats remain explicit, and successful formats remain downloadable.
    """
    formats = requested_formats(request.query)
    if not formats:
        return []
    if (response.metadata.get('awaiting_input') or response.metadata.get('awaiting_approval')
            or response.metadata.get('awaiting_clarification')
            or not is_complete(response.finish_reason, response.answer)):
        return []
    from pwanimate.orchestrator.activity import emit
    from pwanimate.tools.domain.exports import GenerateDocumentTool
    files, failures = [], []
    from documents.documents.models import Document
    reused = {}
    for document in Document.objects.filter(pk__in=response.metadata.pop('_document_ids', []), uploaded_by=request.user, is_ai_generated=True):
        file = document.latest_version.files.first() if document.latest_version else None
        if file: reused[document.generated_format] = resource_payload(document, file)
    tool = GenerateDocumentTool()
    for fmt in formats:
        if cancel_check and cancel_check():
            response.metadata['export_cancelled'] = True
            break
        emit(on_progress, 'tool', '', tool='generate_document', event_id='export-' + fmt, status='running')
        try:
            result = tool.execute(request.user, format=fmt, source_message_id=message.pk,
                request_message_id=request.user_message_id, _finalizing=True) if fmt not in reused else None
            if result is not None and not result.success:
                raise ValueError(result.error or 'Export failed')
            # Recheck before publishing a link; cancellation never becomes success.
            if cancel_check and cancel_check():
                response.metadata['export_cancelled'] = True
                break
            files.append(result.data if result is not None else reused[fmt])
            emit(on_progress, 'tool', '', tool='generate_document', event_id='export-' + fmt, status='completed')
        except Exception as exc:
            # Fixed user-facing text; no private converter paths/tool payloads.
            failures.append({'format': fmt, 'message': f'{fmt.upper()} could not be created. Retry the download for this saved answer.',
                'error_type': type(exc).__name__})
            emit(on_progress, 'tool', '', tool='generate_document', event_id='export-' + fmt, status='failed')
    response.metadata['generated_documents'] = files
    response.metadata['export_errors'] = failures
    response.metadata['artifacts_complete'] = len(files) == len(formats) and not failures
    return files
