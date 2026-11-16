from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from pwanimate.models import PwanimateConversation
from pwanimate.services.study_context import resolve,select,request_tracking,StudyConflict

class StudyContextView(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request,conversation_id):
        conv=get_object_or_404(PwanimateConversation,pk=conversation_id,user=request.user)
        return Response(resolve(request.user,conv).payload(),headers={'Cache-Control':'private, no-store'})
    def post(self,request,conversation_id):
        conv=get_object_or_404(PwanimateConversation,pk=conversation_id,user=request.user)
        try:
            if request.data.get('action')=='request_tracking':
                actions=request_tracking(request.user,conv,request.data.get('generation'))
                return Response({'actions':actions,'study_context':resolve(request.user,conv).payload()})
            context=select(request.user,conv,request.data.get('session_id'),request.data.get('generation'))
            return Response(context.payload())
        except StudyConflict as exc:
            return Response({'error':str(exc),'study_context':resolve(request.user,conv).payload()},status=409)
