"""Protect linked historical sessions at the existing API entry points too."""
from rest_framework.exceptions import PermissionDenied


class AcademicSessionWriteGuard:
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.method in {'GET', 'HEAD', 'OPTIONS'}:
            return
        from pwanimate.models import PwanimateStudySession
        from pwanimate.services.unit_learning import UnitLearningService
        sessions = PwanimateStudySession.objects.filter(user=request.user, learning_record__isnull=False)
        if kwargs.get('session_id'):
            sessions = sessions.filter(pk=kwargs['session_id'])
        elif request.data.get('conversation_id'):
            import uuid
            try:
                conversation_id = uuid.UUID(str(request.data['conversation_id']))
            except (ValueError, TypeError):
                return  # The existing endpoint validates malformed identifiers.
            sessions = sessions.filter(conversation_id=conversation_id)
        else:
            return
        for session in sessions.select_related('learning_record'):
            try:
                UnitLearningService.require_writable(session.learning_record, request.user)
            except ValueError as exc:
                raise PermissionDenied(str(exc)) from exc
