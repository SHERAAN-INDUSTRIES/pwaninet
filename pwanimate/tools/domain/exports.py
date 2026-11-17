"""File generation through Spark's existing private resource exporter."""
from pwanimate.tools.base import BaseDomainTool, ToolResult
from pwanimate.services.document_exports import requested_formats, resource_payload
from pwanimate.services.export_formats import FORMATS


class GenerateDocumentTool(BaseDomainTool):
    name = 'generate_document'
    description = (
        'Create an actual private PDF, DOCX, legacy DOC, or PPTX download from an owned completed assistant response. '
        'For newly requested notes/summaries/papers, omit source_message_id: prepare the export and write the full document '
        'content in the final answer. The application invokes this tool after that completed answer is saved and returns '
        'real download cards. Do not invent URLs or say a queued export has already finished. '
        'For a previously completed response, supply its known message ID. Never invent IDs. '
        'The format must be explicitly requested by the student. Files are not official exams or evidence of mastery.')
    parameters_schema = {'type': 'object', 'properties': {
        'format': {'type': 'string', 'enum': list(FORMATS)},
        'source_message_id': {'type': 'integer', 'minimum': 1}}, 'required': ['format']}

    def execute(self, user, format, source_message_id=None, request_message_id=None, _finalizing=False, **kwargs):
        from pwanimate.models import PwanimateMessage, PwanimateTurnTrace, SparkAgentRun
        if not getattr(user, 'is_authenticated', False):
            return ToolResult.fail('Sign in to generate a document.')
        source = PwanimateMessage.objects.filter(pk=request_message_id, role='user', conversation__user=user).first()
        if source is None or format not in requested_formats(source.content):
            return ToolResult.fail('An owned explicit document request is required for this format.')
        if source_message_id is None:
            return ToolResult.ok({'status': 'awaiting_completed_content', 'format': format,
                'notice': 'Export is prepared, not created yet. Write the actual document content in the final answer.'})
        message = PwanimateMessage.objects.select_related('conversation').filter(pk=source_message_id,
            role='assistant', conversation=source.conversation, conversation__user=user).first()
        if message is None:
            return ToolResult.fail('The source response is unavailable.')
        if not _finalizing:
            completed = PwanimateTurnTrace.objects.filter(assistant_message=message,
                metadata__response_complete=True, metadata__finish_reason='stop').exists() or SparkAgentRun.objects.filter(user=user,
                result_message=message, state__result_completion__response_complete=True, state__result_completion__message_id=message.pk).exists()
            if not completed:
                return ToolResult.fail('Only a verified completed response can be exported automatically.')
        from pwanimate.services.generated_documents import create_generated_resource
        document, file, _ = create_generated_resource(message, user, format)
        payload = resource_payload(document, file)
        if not _finalizing:
            payload.pop('download_url', None)  # Signed links belong in final transport, not planner prompts.
        return ToolResult.ok(payload)
