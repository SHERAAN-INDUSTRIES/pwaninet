"""
Tool Context Adapter for Pwanimate.

Converts raw ToolResult dictionaries into standard ContextItem and ContextPackage
abstractions, preserving canonical citations, provenance URLs, and untrusted
data grounding boundaries.
"""

from typing import Any, Dict, List, Optional
from pwanimate.context.types import ContextItem, ContextPackage
from pwanimate.tools.base import ToolResult


def tool_result_to_context_items(tool_name: str, result: ToolResult) -> List[ContextItem]:
    """
    Convert a ToolResult into one or more grounded ContextItem instances.
    Guarantees that all output data consists of JSON-serializable primitives.
    """
    if not result.success:
        return [
            ContextItem(
                source="system",
                object_id=f"tool_notice_{tool_name}",
                title=f"Tool Notice ({tool_name})",
                content=f"Notice: {result.error or 'The requested domain information could not be retrieved.'}",
                citation=None,
                url="",
            )
        ]

    data = result.data

    # Public web results keep their original URLs so citations can be rendered
    # and opened through the existing source-summary UI.
    if tool_name == "web_search":
        if not isinstance(data, list) or not data:
            return [
                ContextItem(
                    source="web",
                    object_id="web_search_none",
                    title="Web Search",
                    content="The web search returned no results for this request.",
                    citation=None,
                    url="",
                    metadata={"resource_type": "web_search"},
                )
            ]
        items = []
        for index, entry in enumerate(data[:10], start=1):
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title") or "Web result")[:500]
            url = str(entry.get("url") or "")[:2048]
            source = str(entry.get("source") or "")[:253]
            snippet = str(entry.get("snippet") or "")[:2400]
            published_at = entry.get("published_at")
            score = entry.get("score")
            lines = [f"Source: {source}" if source else "Source: public web"]
            if published_at:
                lines.append(f"Published: {str(published_at)[:120]}")
            if url:
                lines.append(f"URL: {url}")
            if snippet:
                lines.extend(["Snippet:", snippet])
            metadata = {
                "resource_type": "web_search",
                "source": source,
                "published_at": published_at,
                "snippet": snippet,
                "web_citation_index": index,
            }
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                metadata["score"] = score
            items.append(ContextItem(
                source="web",
                object_id=url or f"web_result_{index}",
                title=title,
                content="\n".join(lines),
                citation=f"[Web {index}: {source or title[:80]}]",
                url=url,
                relevance_score=score if isinstance(score, (int, float)) else 0.0,
                metadata=metadata,
            ))
        return items or [
            ContextItem(
                source="web",
                object_id="web_search_none",
                title="Web Search",
                content="The web search returned no usable results for this request.",
                citation=None,
                url="",
                metadata={"resource_type": "web_search"},
            )
        ]

    # 1. Academic Lookup Tool
    if tool_name == "academic_lookup":
        if not data or not isinstance(data, list):
            return [
                ContextItem(
                    source="academic",
                    object_id="academic_none",
                    title="Academic Lookup",
                    content="No matching academic programmes, departments, or courses found.",
                    citation=None,
                    url="/academic/",
                )
            ]
        items = []
        for entry in data:
            entity_type = entry.get("entity_type", "entity").title()
            name = entry.get("name", "")
            code = entry.get("code") or ""
            desc = entry.get("description") or "No description provided."
            level = f"Academic Level: {entry.get('academic_level')}\n" if entry.get("academic_level") else ""
            duration = f"Duration: {entry.get('duration_years')} years\n" if entry.get("duration_years") else ""
            dept = f"Department: {entry.get('department_name')}\n" if entry.get("department_name") else ""
            school = f"School: {entry.get('school_name') or entry.get('school_code')}\n" if entry.get("school_name") or entry.get("school_code") else ""

            content_lines = [
                f"{entity_type}: {name} ({code})" if code else f"{entity_type}: {name}",
                f"{level}{duration}{dept}{school}Description: {desc}".strip(),
            ]

            citation = f"[{code}]" if code else f"[{name}]"
            slug = entry.get("slug") or ""
            url = f"/academic/{entry.get('entity_type')}/{slug}/" if slug else "/academic/"

            items.append(
                ContextItem(
                    source="academic",
                    object_id=code or str(entry.get("id")),
                    title=f"{entity_type}: {name}",
                    content="\n".join(content_lines),
                    citation=citation,
                    url=url,
                    metadata=entry,
                )
            )
        return items

    # 2. Document Detail Tool
    elif tool_name == "document_detail":
        if not data or not isinstance(data, dict):
            return [
                ContextItem(
                    source="document",
                    object_id="doc_unknown",
                    title="Document Detail",
                    content="Document details could not be found.",
                    citation=None,
                    url="/documents/",
                )
            ]
        title = data.get("title", "Document")
        desc = data.get("description") or "No description provided."
        category = data.get("category") or "General"
        views = data.get("view_count", 0)
        downloads = data.get("download_count", 0)
        share_id = data.get("share_id", "")
        url = data.get("canonical_url") or f"/documents/document/{share_id}/"

        content = (
            f"Title: {title}\n"
            f"Category: {category}\n"
            f"Engagement: {views} views, {downloads} downloads\n"
            f"Description: {desc}"
        )

        return [
            ContextItem(
                source="document",
                object_id=share_id,
                title=title,
                content=content,
                citation=f"[{title}]",
                url=url,
                metadata=data,
            )
        ]

    # 3. Group Announcements Tool
    elif tool_name == "group_announcements":
        if not data or not isinstance(data, dict):
            return [
                ContextItem(
                    source="announcement",
                    object_id="announcements_none",
                    title="Group Announcements",
                    content="Group announcements could not be retrieved.",
                    citation=None,
                    url="/groups/",
                )
            ]
        group_name = data.get("group_name", "Group")
        announcements = data.get("announcements", [])

        if not announcements:
            return [
                ContextItem(
                    source="announcement",
                    object_id=f"group_{data.get('group_id')}",
                    title=f"Announcements: {group_name}",
                    content=f"There are currently no recent announcements in the '{group_name}' group.",
                    citation=f"[{group_name}]",
                    url=f"/groups/{data.get('group_id')}/",
                    metadata=data,
                )
            ]

        items = []
        for post in announcements:
            author = post.get("author_display_name") or post.get("author") or "Author"
            content = post.get("content") or "No text content."
            share_id = post.get("share_id", "")
            created_at = post.get("created_at", "")

            body = f"Author: {author}\nDate: {created_at}\nAnnouncement: {content}"
            items.append(
                ContextItem(
                    source="announcement",
                    object_id=share_id or str(post.get("id")),
                    title=f"Announcement in {group_name} by {author}",
                    content=body,
                    citation=f"[{group_name} Announcement]",
                    url=post.get("canonical_url", f"/post/{share_id}/"),
                    metadata=post,
                )
            )
        return items

    # 4. User Profile Tool
    elif tool_name == "user_profile":
        if not data or not isinstance(data, dict):
            return [
                ContextItem(
                    source="user",
                    object_id="user_none",
                    title="User Profile",
                    content="User profile could not be found.",
                    citation=None,
                    url="/users/",
                )
            ]
        username = data.get("username", "user")
        display_name = data.get("display_name", username)
        role = data.get("global_role", "Normal")
        headline = f"Headline: {data['headline']}\n" if data.get("headline") else ""
        bio = f"Bio: {data['bio']}\n" if data.get("bio") else ""
        prog = data.get("programme_name") or data.get("programme") or data.get("course")
        course = f"Programme/Course: {prog}\n" if prog else ""
        level_str = data.get("academic_level") or (f"Year {data.get('year')}" if data.get("year") else None)
        academic_level = f"Academic Level: {level_str}\n" if level_str else ""
        interests = f"Interests: {data['interests']}\n" if data.get("interests") else ""
        collab = f"Collaboration Status: {data['collaboration_status']}\n" if data.get("collaboration_status") else ""
        skills = f"Skills: {', '.join(data['skills'])}\n" if data.get("skills") else ""
        email = f"Email: {data['email']}\n" if data.get("email") else ""

        body = (
            f"User: {display_name} (@{username})\n"
            f"Role: {role}\n"
            f"{headline}{bio}{course}{academic_level}{interests}{collab}{skills}{email}".strip()
        )

        profile_url = data.get("profile_url") or data.get("canonical_url") or f"/users/user/{username}/"

        return [
            ContextItem(
                source="user",
                object_id=username,
                title=f"Profile of {display_name} (@{username})",
                content=body,
                citation=f"[@{username}]",
                url=profile_url,
                metadata=data,
            )
        ]

    # 5. Notification Summary Tool
    elif tool_name == "notification_summary":
        if not data or not isinstance(data, dict):
            return [
                ContextItem(
                    source="notification",
                    object_id="notif_none",
                    title="Notification Summary",
                    content="No notifications found.",
                    citation=None,
                    url="/notifications/",
                )
            ]
        unread_count = data.get("unread_count", 0)
        total_count = data.get("total_count")
        notifications = data.get("notifications", [])

        lines = [f"You have {unread_count} unread notification(s)."]
        if total_count is not None:
            lines.append(f"You have {total_count} total notification(s) in your inbox.")
        if notifications:
            lines.append("Recent notifications:")
            for n in notifications:
                lines.append(f"- [{n.get('type')}] {n.get('title')}: {n.get('summary', '')} ({n.get('status')})")
        else:
            lines.append("You have no pending notifications at this time.")

        return [
            ContextItem(
                source="notification",
                object_id="notification_summary",
                title=f"Personal Notifications ({unread_count} unread, {total_count if total_count is not None else 'unknown'} total)",
                content="\n".join(lines),
                citation="[Notifications]",
                url="/notifications/",
                metadata=data,
            )
        ]

    # 6. People Discovery Tool
    elif tool_name == "people_discovery":
        if not data or not isinstance(data, list):
            return [
                ContextItem(
                    source="user",
                    object_id="people_none",
                    title="People Discovery",
                    content="No matching students or peers were found for the requested criteria.",
                    citation=None,
                    url="/users/",
                )
            ]
        items = []
        for person in data:
            username = person.get("username", "user")
            display_name = person.get("display_name", username)
            headline = f"Headline: {person['headline']}\n" if person.get("headline") else ""
            programme = f"Programme: {person['programme_name']}\n" if person.get("programme_name") else ""
            level = f"Level: {person['academic_level']}\n" if person.get("academic_level") else ""
            status = f"Collaboration: {person['collaboration_status']}\n" if person.get("collaboration_status") else ""
            skills = f"Matched Skills: {', '.join(person['matched_skills'])}\n" if person.get("matched_skills") else ""
            interests = f"Matched Interests: {', '.join(person['matched_interests'])}\n" if person.get("matched_interests") else ""

            evidence = person.get("evidence", {})
            evidence_lines = []
            if evidence.get("academic_alignment"):
                evidence_lines.append(f"Connection: {evidence['academic_alignment']}")
            if evidence.get("shared_groups_count", 0) > 0:
                evidence_lines.append(f"Shared Groups: {evidence['shared_groups_count']}")
            if evidence.get("mutual_connections_count", 0) > 0:
                evidence_lines.append(f"Mutual Connections: {evidence['mutual_connections_count']}")
            evidence_str = f"Context: {'; '.join(evidence_lines)}\n" if evidence_lines else ""

            body = (
                f"Peer: {display_name} (@{username})\n"
                f"{headline}{programme}{level}{status}{skills}{interests}{evidence_str}".strip()
            )

            url = person.get("profile_url") or f"/users/user/{username}/"

            items.append(
                ContextItem(
                    source="user",
                    object_id=f"user_{username}",
                    title=f"{display_name} (@{username})",
                    content=body,
                    citation=f"[@{username}]",
                    url=url,
                    metadata=person,
                )
            )
        return items

    # 7. Site Info Tool
    elif tool_name == "site_info":
        if not data or not isinstance(data, dict):
            return [
                ContextItem(
                    source="site_content",
                    object_id="site_info_none",
                    title="Site Information",
                    content="Site information could not be retrieved.",
                    citation=None,
                    url="/about/",
                )
            ]
        content = data.get("content")
        topic = data.get("topic", "site")
        if not content:
            available = data.get("available_topics", [])
            return [
                ContextItem(
                    source="site_content",
                    object_id=f"site_{topic}",
                    title=f"Site Information: {topic}",
                    content=f"No specific information found for '{topic}'. Available topics: {', '.join(available)}",
                    citation="[vasto Site Info]",
                    url="/about/",
                    metadata=data,
                )
            ]
        return [
            ContextItem(
                source="site_content",
                object_id=f"site_{topic}",
                title=f"vasto: {topic.replace('_', ' ').title()}",
                content=content[:5000],
                citation="[vasto Site Documentation]",
                url="/about/",
                metadata=data,
            )
        ]

    # 8. Write actions (Spark proposes; the student approves)
    elif tool_name in ("send_message", "follow_user"):
        action = data.get("action") if isinstance(data, dict) else None
        summary = ""
        recipient = ""
        if isinstance(action, dict):
            summary = action.get("summary") or ""
        if isinstance(data, dict):
            recipient = data.get("recipient") or ""
        if not summary:
            return [
                ContextItem(
                    source="tool_action",
                    object_id="tool_action_none",
                    title="Proposed Action",
                    content="The action could not be prepared.",
                    citation=None,
                    url="",
                )
            ]
        return [
            ContextItem(
                source="tool_action",
                object_id=(action or {}).get("id", "tool_action"),
                title=f"Proposed action for @{recipient}" if recipient else "Proposed action",
                content=(
                    f"{summary}\n\n"
                    "This action has NOT been performed. The student must approve it "
                    "in the app before anything happens. Tell them plainly what you "
                    "proposed and that it needs their approval -- do not claim it was "
                    "done, and do not repeat the action."
                ),
                citation=None,
                url="",
                metadata=data,
            )
        ]

    # Fallback for unknown tool
    return [
        ContextItem(
            source=tool_name,
            object_id="tool_generic",
            title=f"Tool Output: {tool_name}",
            content=str(data),
            citation=f"[{tool_name}]",
            url="",
        )
    ]


def build_tool_context_package(
    query: str,
    items: List[ContextItem],
    user_context: Optional[Any] = None,
) -> ContextPackage:
    """
    Assemble a list of ContextItems into a bounded ContextPackage ready for
    injection into the LLM system/user grounding prompt.
    """
    citations = []
    source_counts: Dict[str, int] = {}
    total_chars = 0
    total_tokens = 0

    for item in items:
        if item.citation and item.citation not in citations:
            citations.append(item.citation)
        src_str = str(item.source.value if hasattr(item.source, "value") else item.source)
        source_counts[src_str] = source_counts.get(src_str, 0) + 1
        total_chars += len(item.content)
        total_tokens += item.estimated_tokens

    return ContextPackage(
        query=query,
        items=items,
        citations=citations,
        total_items=len(items),
        estimated_tokens=total_tokens,
        total_characters=total_chars,
        truncated=False,
        source_counts=source_counts,
        user_context=user_context,
    )
