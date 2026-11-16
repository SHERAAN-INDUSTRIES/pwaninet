"""Document domain permissions for REST API endpoints."""

from rest_framework import permissions
from users.models import GlobalRole


class IsDocumentUploaderOrAdmin(permissions.BasePermission):
    """
    Object-level permission allowing:
    - Safe read access to all for public ready documents.
    - Safe read access for uploader/staff/president/delegate for non-public or non-ready documents.
    - Write/Update/Delete access ONLY to the document uploader, staff, superuser,
      or users with presidential/delegate global roles.
    """

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        from documents.access_policy import DocumentAccessPolicy
        if request.method in permissions.SAFE_METHODS:
            return DocumentAccessPolicy.can_read(request.user,obj,require_ready=False)
        if obj.visibility == 'private' or obj.is_ai_generated:
            return bool(request.user.is_authenticated and obj.uploaded_by_id == request.user.pk)
        return bool(request.user.is_authenticated and (
            obj.uploaded_by_id == request.user.pk or request.user.is_staff or request.user.is_superuser
            or getattr(request.user,'global_role',None) in [GlobalRole.PRESIDENT,GlobalRole.DELEGATE]))
