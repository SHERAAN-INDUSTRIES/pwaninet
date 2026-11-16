"""Owner-scoped follow-up execution and stable replay."""
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from pwanimate.services.followups import resolve_follow_up, execute_follow_up_action, FollowUpError

class FollowUpExecuteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, follow_up_id):
        try:
            item = resolve_follow_up(follow_up_id, request.user)
            if request.data.get('conversation_id') and str(item.conversation_id) != str(request.data['conversation_id']):
                raise FollowUpError('Conversation mismatch.', 404)
            result = execute_follow_up_action(item, request.user)
            return Response(result, status=200 if result.get('executed') else 202)
        except FollowUpError as exc:
            return Response({'error': str(exc)}, status=exc.code)
        except Exception:
            return Response({'error': 'Spark could not finish. Retry this suggestion.'}, status=503)
