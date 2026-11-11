"""
Group Announcements Tool for Pwanimate.

Allows deterministic, read-only retrieval of group posts and announcements
respecting group membership and visibility policies.
"""

from typing import Any, Dict, List, Optional

from pwanimate.tools.base import BaseDomainTool, ToolResult
from pwanimate.tools.exceptions import ToolValidationError, ToolPermissionError, ToolNotFoundError
from groups.models import Group, PostVisibility
from groups.queries.group_queries import is_group_member, get_group_posts


class GroupAnnouncementsTool(BaseDomainTool):
    """
    Retrieve announcements and posts from a vasto group.
    """
    name = "group_announcements"
    description = (
        "Retrieve recent posts and announcements from a vasto group. "
        "Enforces group membership requirements for members-only groups."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "group_id": {
                "type": "integer",
                "description": "Unique integer ID of the group",
            },
            "group_name": {
                "type": "string",
                "description": "Exact name of the group",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of announcements to return (default 5, max 20)",
                "default": 5,
            },
        },
        "required": [],
    }

    def execute(
        self,
        user: Any,
        group_id: Optional[int] = None,
        group_name: Optional[str] = None,
        limit: int = 5,
        **kwargs,
    ) -> ToolResult:
        if not group_id and not group_name:
            raise ToolValidationError(
                "Either 'group_id' or 'group_name' must be provided.",
                tool_name=self.name,
            )

        limit = min(max(1, int(limit)), 20)

        group = None
        if group_id:
            group = Group.objects.filter(id=group_id).first()
        elif group_name:
            group = Group.objects.filter(name__iexact=group_name.strip()).first()

        if not group:
            raise ToolNotFoundError(
                f"Group not found (group_id={group_id}, group_name='{group_name}').",
                tool_name=self.name,
            )

        # Authorization: check if post visibility is members-only
        is_auth = user is not None and getattr(user, "is_authenticated", False)
        is_staff = is_auth and getattr(user, "is_staff", False)

        if group.post_visibility == PostVisibility.MEMBERS_ONLY:
            if not is_auth or (not is_staff and not is_group_member(group, user)):
                raise ToolPermissionError(
                    f"Access denied: you must be an approved member of '{group.name}' to view announcements.",
                    tool_name=self.name,
                )

        # Fetch posts using existing domain query helper
        posts_qs = get_group_posts(group, user)[:limit]

        announcements: List[Dict[str, Any]] = []
        for post in posts_qs:
            author_name = post.author.username if post.author else "Unknown"
            display_name = ""
            if post.author:
                display_name = (
                    f"{post.author.first_name} {post.author.last_name}".strip()
                    or post.author.username
                )

            announcements.append({
                "id": post.id,
                "share_id": str(post.share_id),
                "author": author_name,
                "author_display_name": display_name,
                "content": post.content or "",
                "created_at": post.created_at.isoformat() if hasattr(post, "created_at") and post.created_at else None,
                "canonical_url": f"/post/{post.share_id}/",
            })

        return ToolResult.ok(
            {
                "group_id": group.id,
                "group_name": group.name,
                "is_official": group.is_official,
                "post_visibility": group.post_visibility,
                "announcements": announcements,
            },
            count=len(announcements),
        )
