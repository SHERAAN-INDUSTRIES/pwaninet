"""
Deterministic Tool Router for Pwanimate.

Provides high-confidence, application-side intent matching and parameter extraction
for Pwanimate domain tools. Prioritizes conservative false negatives (falling back
to Unified Retrieval) over false positives (invoking the wrong tool).
"""

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


@dataclass
class ToolRoute:
    """
    Result of a successful high-confidence tool route match.

    Attributes:
        tool_name: Registered name of the domain tool.
        parameters: Validated keyword arguments for tool execution.
        confidence: Confidence score (1.0 for exact pattern matches).
        matched_intent: Descriptive label of the matched intent.
    """
    tool_name: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    matched_intent: str = ""


class ToolRouter:
    """
    Evaluates natural-language user queries to determine if an authorized domain tool
    should be deterministically invoked instead of broad retrieval.
    """

    # 1. Notification Patterns (first-person notifications only)
    NOTIFICATION_PATTERNS = [
        r"^(?:show\s+me\s+|what\s+are\s+|check\s+)?(?:all\s+)?my\s+unread\s+notifications[\s\?\.]*$",
        r"^(?:show\s+me\s+|what\s+are\s+|check\s+)?(?:all\s+)?my\s+notifications[\s\?\.]*$",
        r"^do\s+i\s+have\s+(?:any\s+)?(?:new\s+|unread\s+)?notifications[\s\?\.]*$",
        r"^how\s+many\s+unread\s+notifications\s+(?:do\s+i\s+have)?[\s\?\.]*$",
        r"^how\s+many\s+(?:all\s+)?(?:my\s+)?notifications(?:\s+do\s+i\s+have)?[\s\?\.]*$",
        r"^(?:count|what(?:'s|\s+is)\s+the\s+count\s+of)\s+(?:all\s+)?(?:my\s+)?notifications[\s\?\.]*$",
        r"^what\s+notifications\s+do\s+i\s+have[\s\?\.]*$",
    ]

    # 2. User Profile Patterns (requires explicit @username to avoid ambiguous name guessing)
    USER_PROFILE_PATTERNS = [
        # Standard lookups
        r"^(?:who\s+is|tell\s+me\s+about|can\s+you\s+tell\s+me\s+about|lookup|show\s+me|view|info\s+(?:on|about)|information\s+(?:on|about))\s+@([a-zA-Z0-9_\-\.]+)[\s\?\.]*$",
        r"^(?:profile\s+(?:of|for)|user\s+profile\s+for)\s+@?([a-zA-Z0-9_\-\.]+)[\s\?\.]*$",
        r"^@([a-zA-Z0-9_\-\.]+)[\s\?\.]*$",
        # Academic / study questions
        r"^(?:what\s+(?:does|is)\s+)?@([a-zA-Z0-9_\-\.]+)\s+(?:study(?:ing)?|enrolled\s+in|taking)[\s\?\.]*$",
        r"^what\s+(?:programme|program|course|degree|year|level)\s+is\s+@([a-zA-Z0-9_\-\.]+)(?:\s+in|\s+taking)?[\s\?\.]*$",
        r"^(?:what\s+is\s+)?(?:the\s+)?(?:programme|program|course|degree|academic\s+level|year)\s+(?:of|for)\s+@([a-zA-Z0-9_\-\.]+)[\s\?\.]*$",
        # Skills & expertise questions
        r"^(?:what\s+(?:skills?\s+)?(?:does|do)\s+)?@([a-zA-Z0-9_\-\.]+)\s+(?:have|know|possess|specialize\s+in)[\s\?\.]*$",
        r"^what\s+are\s+(?:the\s+)?skills?\s+(?:of|for)\s+@([a-zA-Z0-9_\-\.]+)[\s\?\.]*$",
        r"^what\s+are\s+@([a-zA-Z0-9_\-\.]+)(?:'s|\s+)?skills?[\s\?\.]*$",
        r"^does\s+@([a-zA-Z0-9_\-\.]+)\s+know\s+([^?\.]+?)[\s\?\.]*$",
        # Interests questions
        r"^(?:what\s+is\s+)?@([a-zA-Z0-9_\-\.]+)\s+interested\s+in[\s\?\.]*$",
        r"^what\s+are\s+(?:the\s+)?interests?\s+(?:of|for)\s+@([a-zA-Z0-9_\-\.]+)[\s\?\.]*$",
        r"^what\s+are\s+@([a-zA-Z0-9_\-\.]+)(?:'s|\s+)?interests?[\s\?\.]*$",
        # Collaboration availability questions
        r"^is\s+@([a-zA-Z0-9_\-\.]+)\s+(?:open\s+to|available\s+for)\s+(?:collaboration|projects?|study\s+groups?|networking)[\s\?\.]*$",
        r"^(?:what\s+is\s+)?(?:the\s+)?collaboration\s+status\s+(?:of|for)\s+@([a-zA-Z0-9_\-\.]+)[\s\?\.]*$",
        # Projects inquiry for user
        r"^(?:what\s+projects?\s+(?:is|does)\s+)?@([a-zA-Z0-9_\-\.]+)\s+(?:working\s+on|have)[\s\?\.]*$",
        r"^does\s+@([a-zA-Z0-9_\-\.]+)\s+have\s+(?:any\s+)?projects?[\s\?\.]*$",
    ]

    # 3. Document Detail Patterns (explicit share_id UUID or explicit document slug)
    DOC_UUID_PATTERN = r"(?:tell\s+me\s+about|details?\s+(?:of|for)|what\s+is\s+in)\s+document\s+([0-9a-fA-F\-]{32,36})"
    DOC_SLUG_PATTERN = r"(?:tell\s+me\s+about|details?\s+(?:of|for)|what\s+is\s+in)\s+document\s+([a-z0-9\-]{3,80})[\s\?\.]*$"

    # 4. Group Announcements Patterns
    GROUP_ANNOUNCEMENT_PATTERNS = [
        r"^(?:what\s+are\s+the\s+|what\s+)?announcements?\s+(?:are\s+)?in\s+([a-zA-Z0-9\s\-]+?)(?:\s+group|\s+club)?[\s\?\.]*$",
        r"^(?:what\s+was\s+posted|latest\s+posts?)\s+in\s+([a-zA-Z0-9\s\-]+?)(?:\s+group|\s+club)?[\s\?\.]*$",
    ]

    # 5. Academic Lookup Patterns (explicit PU statutory codes or programme structure)
    ACADEMIC_CODE_PATTERN = r"^(?:what\s+is|tell\s+me\s+about|lookup)\s+([A-Z]{2,6}(?:-[A-Z0-9]{2,10})?)[\s\?\.]*$"
    ACADEMIC_STRUCTURE_PATTERN = r"^(?:structure|curriculum|units)\s+(?:of|for|in)\s+([a-zA-Z0-9\s\-]+?)\s+(?:programme|program|department|school)[\s\?\.]*$"

    def route(
        self,
        query: str,
        user: Any = None,
        user_context: Optional[Any] = None,
        local_time: Optional[str] = None,
        timezone_name: Optional[str] = None,
    ) -> Optional[ToolRoute]:
        """
        Evaluate query against high-confidence patterns.
        Returns a ToolRoute if matched with high confidence, or None.
        """
        if not query or not isinstance(query, str):
            return None

        clean_query = query.strip()
        lower_query = clean_query.lower()

        if re.match(
            r"^(?:what(?:'s|s| is)\s+(?:the\s+)?(?:current\s+)?time(?:\s+right\s+now)?|what\s+time\s+is\s+it(?:\s+(?:right\s+now|in\s+(?:kenya|nairobi)))?|current\s+time(?:\s+in\s+(?:kenya|nairobi))?|time\s+now|what\s+time\s+now)[\s?!\.]*$",
            lower_query,
        ):
            return ToolRoute("local_time", {"local_time": local_time or "", "timezone_name": timezone_name or "UTC"}, matched_intent="local_time")

        m_search = re.match(
            r"^(?:(?:have|can|could)\s+you\s+)?(?:seen|search(?:\s+for)?|find|look\s+for|show\s+me)\s+(?:the\s+)?(posts?|documents?|docs?)\s+(?:with\s+(?:the\s+)?(?:content|text)\s+|(?:about|for|on|containing)\s+)(.+?)[\s?.!]*$",
            clean_query, re.I,
        )
        if m_search:
            kind = m_search.group(1).lower()
            return ToolRoute("content_search", {"query": m_search.group(2).strip(), "content_type": "post" if kind.startswith("post") else "document", "limit": 5}, matched_intent="content_search")

        m_notify = re.match(r"^(?:notify\s+me\s+(?:that\s+)?|send\s+me\s+a\s+notification\s+(?:saying|that)\s+)(.+)$", clean_query, re.I)
        if m_notify and not re.search(r"\b(tomorrow|later|next|at|in\s+\d+)\b",m_notify.group(1),re.I):
            return ToolRoute("send_notification", {"summary": m_notify.group(1).strip()}, matched_intent="send_notification")

        # 1. Notifications check
        route = self._match_notifications(clean_query, lower_query)
        if route:
            return route

        # 2. User Profile check
        route = self._match_user_profile(clean_query)
        if route:
            return route

        # 3. Document Detail check
        route = self._match_document_detail(clean_query)
        if route:
            return route

        # 4. Group Announcements check
        route = self._match_group_announcements(clean_query)
        if route:
            return route

        # 5. Academic Lookup check
        route = self._match_academic_lookup(clean_query)
        if route:
            return route

        # 6. People Discovery check
        route = self._match_people_discovery(clean_query, lower_query)
        if route:
            return route

        # 7. Site Info check
        route = self._match_site_info(clean_query, lower_query)
        if route:
            return route

        return None

    def _match_notifications(self, query: str, lower_query: str) -> Optional[ToolRoute]:
        for pattern in self.NOTIFICATION_PATTERNS:
            if re.match(pattern, lower_query, re.IGNORECASE):
                asks_unread = bool(re.search(r"\bunread\b", lower_query))
                asks_all = bool(re.search(r"\ball\b", lower_query))
                asks_count = bool(re.search(r"\b(?:how\s+many|count)\b", lower_query))
                unread_only = asks_unread or not (asks_all or asks_count)
                return ToolRoute(
                    tool_name="notification_summary",
                    parameters={"unread_only": unread_only, "limit": 5},
                    confidence=1.0,
                    matched_intent="notification_summary",
                )
        return None

    def _match_user_profile(self, query: str) -> Optional[ToolRoute]:
        for pattern in self.USER_PROFILE_PATTERNS:
            m = re.match(pattern, query, re.IGNORECASE)
            if m:
                raw_username = m.group(1).strip().lower()
                # Exclude purely common English words that might follow "profile of"
                if raw_username in {"me", "mine", "user", "someone", "system"}:
                    return None
                return ToolRoute(
                    tool_name="user_profile",
                    parameters={"username": raw_username},
                    confidence=1.0,
                    matched_intent="user_profile",
                )

        # Fallback for inquiries targeting an explicit @username
        m_at = re.search(r"(?:^|\s)@([a-zA-Z0-9_\-\.]+)", query)
        if m_at:
            candidate_user = m_at.group(1).strip().lower()
            if candidate_user not in {"me", "mine", "user", "someone", "system"}:
                lower_q = query.lower()
                profile_keywords = (
                    "profile", "who is", "tell me about", "details", "info", "background",
                    "bio", "study", "studying", "course", "programme", "program", "degree",
                    "year", "level", "skill", "skills", "interest", "interests", "collab",
                    "project", "work with", "reach", "contact"
                )
                if any(kw in lower_q for kw in profile_keywords):
                    return ToolRoute(
                        tool_name="user_profile",
                        parameters={"username": candidate_user},
                        confidence=0.95,
                        matched_intent="user_profile_by_mention",
                    )
        return None

    def _match_document_detail(self, query: str) -> Optional[ToolRoute]:
        # Check UUID
        m_uuid = re.search(self.DOC_UUID_PATTERN, query, re.IGNORECASE)
        if m_uuid:
            raw_uuid = m_uuid.group(1).strip()
            try:
                parsed = str(uuid.UUID(raw_uuid))
                return ToolRoute(
                    tool_name="document_detail",
                    parameters={"share_id": parsed},
                    confidence=1.0,
                    matched_intent="document_detail_by_id",
                )
            except ValueError:
                pass

        # Check Slug (must follow "document <slug>")
        # Ensure we do not match general phrases like "document about operating systems"
        m_slug = re.search(self.DOC_SLUG_PATTERN, query, re.IGNORECASE)
        if m_slug:
            slug = m_slug.group(1).strip().lower()
            stop_words = {"about", "for", "on", "in", "related", "with", "summary", "notes"}
            if slug not in stop_words and " " not in slug:
                return ToolRoute(
                    tool_name="document_detail",
                    parameters={"slug": slug},
                    confidence=0.95,
                    matched_intent="document_detail_by_slug",
                )
        return None

    def _match_group_announcements(self, query: str) -> Optional[ToolRoute]:
        for pattern in self.GROUP_ANNOUNCEMENT_PATTERNS:
            m = re.match(pattern, query, re.IGNORECASE)
            if m:
                group_name = m.group(1).strip()
                # Exclude generic search terms
                if group_name.lower() in {"a", "the", "my", "any", "all", "some", "this"}:
                    return None
                return ToolRoute(
                    tool_name="group_announcements",
                    parameters={"group_name": group_name, "limit": 5},
                    confidence=1.0,
                    matched_intent="group_announcements",
                )
        return None

    def _match_academic_lookup(self, query: str) -> Optional[ToolRoute]:
        # 1. Exact uppercase code (e.g. BSC-CS, SPAS, COMP)
        m_code = re.match(self.ACADEMIC_CODE_PATTERN, query)
        if m_code:
            code = m_code.group(1).strip().upper()
            return ToolRoute(
                tool_name="academic_lookup",
                parameters={"code": code, "entity_type": "all"},
                confidence=1.0,
                matched_intent="academic_code_lookup",
            )

        # 2. Programme structure by name
        m_struct = re.match(self.ACADEMIC_STRUCTURE_PATTERN, query, re.IGNORECASE)
        if m_struct:
            prog_name = m_struct.group(1).strip()
            return ToolRoute(
                tool_name="academic_lookup",
                parameters={"query": prog_name, "entity_type": "programme", "limit": 5},
                confidence=0.9,
                matched_intent="academic_structure_lookup",
            )

        return None

    def _match_site_info(self, query: str, lower_query: str) -> Optional[ToolRoute]:
        """Match questions about vasto or Pwanimate itself."""
        site_patterns = [
            r"^(?:what\s+is|about|tell\s+me\s+about|describe)\s+vasto[\s\?\.]*$",
            r"^(?:what\s+is|about|tell\s+me\s+about|describe)\s+pwanimate[\s\?\.]*$",
            r"^(?:what\s+can\s+you\s+do|how\s+can\s+you\s+help(?:\s+me)?|what\s+are\s+your\s+(?:features?|capabilities?))[\s\?\.]*$",
            r"^(?:who\s+are\s+you|what\s+are\s+you)[\s\?\.]*$",
            r"^(?:how\s+does\s+vasto\s+work|how\s+does\s+this\s+(?:site|platform|app)\s+work)[\s\?\.]*$",
            r"^(?:vasto|pwanimate)\s+features?[\s\?\.]*$",
            r"^help[\s\?\.]*$",
        ]
        for pattern in site_patterns:
            if re.match(pattern, lower_query, re.IGNORECASE):
                # Extract topic from query
                topic = "about"
                if "pwanimate" in lower_query:
                    topic = "pwanimate"
                elif "feature" in lower_query or "capabilit" in lower_query or "what can you do" in lower_query:
                    topic = "pwanimate"
                elif "who are you" in lower_query or "what are you" in lower_query:
                    topic = "pwanimate"
                return ToolRoute(
                    tool_name="site_info",
                    parameters={"topic": topic},
                    confidence=1.0,
                    matched_intent="site_info",
                )
        return None

    def _extract_topic_and_status(self, raw_topic: str) -> Tuple[str, Optional[str]]:
        """
        Split a topic string that may contain a trailing collaboration clause.
        e.g. 'tech who are open to collaboration' -> ('tech', 'any_open')
        e.g. 'python and are open to projects' -> ('python', 'open_to_projects')
        e.g. 'Django to work with me' -> ('Django', 'open_to_projects')
        """
        collab_patterns = [
            (r"\s+(?:(?:who|and)\s+(?:is|are)\s+)?(?:open\s+to\s+)?(?:working\s+on\s+)?projects?[\s\?\.]*$", "open_to_projects"),
            (r"\s+(?:(?:who|and)\s+(?:is|are)\s+)?(?:open\s+to\s+)?study\s+groups?[\s\?\.]*$", "open_to_study_groups"),
            (r"\s+(?:(?:who|and)\s+(?:is|are)\s+)?(?:open\s+to\s+)?networking[\s\?\.]*$", "open_to_networking"),
            (r"\s+(?:(?:who|and)\s+(?:is|are)\s+)?open\s+to\s+collaborat(?:e|ion)[\s\?\.]*$", "any_open"),
            (r"\s+(?:who\s+wants?\s+to\s+collaborate|interested\s+in\s+collaborating)[\s\?\.]*$", "any_open"),
            (r"\s+to\s+work\s+with\s+(?:me|us)[\s\?\.]*$", "open_to_projects"),
            (r"\s+to\s+study\s+with\s+(?:me|us)[\s\?\.]*$", "open_to_study_groups"),
            (r"\s+to\s+collaborate\s+with\s+(?:me|us)[\s\?\.]*$", "any_open"),
        ]
        cleaned_topic = raw_topic.strip()
        collab_status = None
        for pattern, status in collab_patterns:
            m = re.search(pattern, cleaned_topic, re.IGNORECASE)
            if m:
                collab_status = status
                cleaned_topic = cleaned_topic[:m.start()].strip()
                break
        cleaned_topic = re.sub(r"^(?:a|an|the)\s+", "", cleaned_topic, flags=re.IGNORECASE).strip()
        return cleaned_topic, collab_status

    def _match_people_discovery(self, query: str, lower_query: str) -> Optional[ToolRoute]:
        """
        Evaluate natural language queries for people discovery, collaboration,
        or skill/interest search.
        """
        # 1. Coursemates / classmates inquiry
        if re.search(r"\b(?:coursemates?|classmates?)\b", lower_query):
            # Check if any skill/topic is mentioned, e.g. "coursemates who know Python"
            m_course_skill = re.search(
                r"\b(?:coursemates?|classmates?)\s+(?:who\s+know|interested\s+in|skilled\s+in)\s+([^.]+?)[\s\?\.]*$",
                query,
                re.IGNORECASE,
            )
            params: Dict[str, Any] = {
                "academic_scope": "my_programme",
                "limit": 10,
                "query": query,
            }
            if m_course_skill:
                raw_topic = m_course_skill.group(1).strip()
                topic, collab_status = self._extract_topic_and_status(raw_topic)
                if topic:
                    params["skills"] = [topic]
                    params["interests"] = [topic]
                if collab_status:
                    params["collaboration_status"] = collab_status
            return ToolRoute(
                tool_name="people_discovery",
                parameters=params,
                confidence=0.95,
                matched_intent="people_coursemates_discovery",
            )

        # 2. Study buddy / study partner / study with / study groups inquiry
        m_study = re.search(
            r"\b(?:study\s+budd(?:y|ies)|study\s+partners?|(?:someone|people|peers?|students?)\s+to\s+study\s+with)\b"
            r"|(?:who\s+is\s+)?(?:open\s+to|available\s+for)\s+study\s+groups?[\s\?\.]*$",
            query,
            re.IGNORECASE,
        )
        if m_study:
            params = {
                "collaboration_status": "open_to_study_groups",
                "limit": 10,
                "query": query,
            }
            m_topic = re.search(
                r"\b(?:study\s+budd(?:y|ies)|study\s+partners?)\s+(?:interested\s+in|who\s+knows?|for|in)\s+([^.]+?)[\s\?\.]*$",
                query,
                re.IGNORECASE,
            )
            if m_topic:
                raw_topic = m_topic.group(1).strip()
                topic, _ = self._extract_topic_and_status(raw_topic)
                if topic and topic.lower() not in ("a", "an", "the", "some"):
                    params["skills"] = [topic]
                    params["interests"] = [topic]
            return ToolRoute(
                tool_name="people_discovery",
                parameters=params,
                confidence=0.95,
                matched_intent="people_study_partner_discovery",
            )

        # 3. Collaboration inquiry: "Who should I collaborate with...", "need collaborators...", "project partner...", "someone to work with..."
        m_collab = (
            re.search(r"\b(?:need|looking\s+for|find(?:\s+me)?|help\s+me\s+find|suggest|recommend|connect\s+me\s+with)\s+(?:a\s+|an\s+|some\s+)?([a-zA-Z0-9\+\#\.\-]+?\s+)?(?:project\s+)?(?:collaborators?|partners?|teammates?)\b", lower_query)
            or re.search(r"\b(?:project\s+partners?|project\s+teammates?|partner\s+(?:for|in|on)\s+(?:a|an|my|the)?\s*projects?)\b", lower_query)
            or re.search(r"\b(?:someone|people|peers?|students?)\s+to\s+work\s+with(?:\s+on\s+(?:a|an|my|the)?\s*projects?)?\b", lower_query)
            or re.search(r"^(?:who\s+(?:should|can|do)\s+i\s+(?:collaborate|work)\s+with)", lower_query)
            or re.search(r"\bcollaborators?\s+(?:within|in|at)\s+(?:the\s+)?(?:uni|university|campus|programme|school)\b", lower_query)
            or re.search(r"\b(?:open\s+to|available\s+for)\s+(?:working\s+on\s+projects?|project\s+work)\b", lower_query)
        )
        if m_collab:
            scope = "my_programme" if ("programme" in lower_query or "program" in lower_query) else None
            params = {
                "query": query,
                "collaboration_status": "open_to_projects",
                "academic_scope": scope,
                "limit": 10,
            }
            # Check for skill prefix before partner/teammate/collaborator, e.g. "Django partner" or "Django project partner"
            m_skill_partner = re.search(r"\b([a-zA-Z0-9\+\#\.\-]+?)\s+(?:project\s+)?(?:partner|teammate|collaborator)[s]?\b", query, re.IGNORECASE)
            if m_skill_partner:
                cand = m_skill_partner.group(1).strip()
                if cand.lower() not in ("a", "an", "the", "my", "our", "project", "study", "good", "new", "some"):
                    params["skills"] = [cand]
                    params["interests"] = [cand]
            return ToolRoute(
                tool_name="people_discovery",
                parameters=params,
                confidence=0.95,
                matched_intent="people_collaboration_recommendation",
            )

        # 4. Open to collaboration / projects / study groups
        m_open_proj = re.match(
            r"^(?:who\s+is|find\s+(?:students|people|peers)\s+(?:who\s+are\s+)?)\s*open\s+to\s+(?:working\s+on\s+)?(?:(?:a|an)\s+)?([a-zA-Z0-9\s\-]+?)\s*project[s]?[\s\?\.]*$",
            query,
            re.IGNORECASE,
        )
        if m_open_proj:
            topic = m_open_proj.group(1).strip()
            topic = re.sub(r"^(?:a|an|the)\s+", "", topic, flags=re.IGNORECASE).strip()
            params = {
                "collaboration_status": "open_to_projects",
                "limit": 10,
                "query": query,
            }
            if topic and topic.lower() not in ("a", "an", "the", "any"):
                params["skills"] = [topic]
                params["interests"] = [topic]
            return ToolRoute(
                tool_name="people_discovery",
                parameters=params,
                confidence=1.0,
                matched_intent="people_open_to_projects",
            )

        # "Who is open to collaboration" / "Find people open to collaboration" / "Find students open to collaboration"
        if re.match(
            r"^(?:who\s+is|find\s+(?:students|people|peers)\s+(?:who\s+are\s+)?)\s*open\s+to\s+collaborat(?:e|ion)[\s\?\.]*$",
            query,
            re.IGNORECASE,
        ):
            return ToolRoute(
                tool_name="people_discovery",
                parameters={"collaboration_status": "any_open", "limit": 10, "query": query},
                confidence=1.0,
                matched_intent="people_open_to_collaboration",
            )

        # 5. Scope relative to viewer:
        # Bare scope: "Find people in my programme", "Find someone in my programme"
        m_prog_exact = re.search(
            r"^(?:find|search|lookup|show\s+me)\s+(?:students|people|peers|classmates|someone)\s+in\s+my\s+(programme|program|department|school)[\s\?\.]*$",
            query,
            re.IGNORECASE,
        )
        if m_prog_exact:
            scope_word = m_prog_exact.group(1).lower()
            scope = "my_programme"
            if "department" in scope_word:
                scope = "my_department"
            elif "school" in scope_word:
                scope = "my_school"
            return ToolRoute(
                tool_name="people_discovery",
                parameters={"academic_scope": scope, "limit": 10, "query": query},
                confidence=0.95,
                matched_intent="people_programme_relative_discovery",
            )

        # Scope + skill: "Find people in my programme who are interested in AI", "Find someone in my programme who's interested in AI"
        m_prog = re.search(
            r"(?:find|search|lookup|show\s+me)\s+(?:students|people|peers|classmates|someone)\s+in\s+my\s+(?:programme|program|department|school)\s+(?:who(?:'s|\s+(?:are|is))\s+)?(?:interested\s+in|who\s+knows?|skilled\s+in)\s+([^.]+?)[\s\?\.]*$",
            query,
            re.IGNORECASE,
        )
        if m_prog:
            raw_topic = m_prog.group(1).strip()
            topic, collab_status = self._extract_topic_and_status(raw_topic)
            scope = "my_programme"
            if "department" in lower_query:
                scope = "my_department"
            elif "school" in lower_query:
                scope = "my_school"
            params = {
                "academic_scope": scope,
                "skills": [topic],
                "interests": [topic],
                "limit": 10,
                "query": query,
            }
            if collab_status:
                params["collaboration_status"] = collab_status
            return ToolRoute(
                tool_name="people_discovery",
                parameters=params,
                confidence=0.95,
                matched_intent="people_programme_relative_discovery",
            )

        # 6. Academic scope + skills / interests
        # e.g. "Find CS students who know Django" or "Find Computer Science students interested in AI"
        m_acad_skill = re.search(
            r"(?:find|search|lookup|show\s+me)\s+([a-zA-Z\s\-]+?)\s+(?:students|peers|classmates|someone)\s+(?:who(?:'s|\s+(?:are|is))\s+)?(?:who\s+knows?|interested\s+in|skilled\s+in)\s+([^.]+?)[\s\?\.]*$",
            query,
            re.IGNORECASE,
        )
        if m_acad_skill:
            scope = m_acad_skill.group(1).strip()
            raw_topic = m_acad_skill.group(2).strip()
            topic, collab_status = self._extract_topic_and_status(raw_topic)
            if scope.lower() not in ("all", "other", "some", "the", "many", "any"):
                params = {
                    "academic_scope": scope,
                    "skills": [topic],
                    "interests": [topic],
                    "limit": 10,
                    "query": query,
                }
                if collab_status:
                    params["collaboration_status"] = collab_status
                return ToolRoute(
                    tool_name="people_discovery",
                    parameters=params,
                    confidence=0.95,
                    matched_intent="people_academic_and_skill_discovery",
                )

        # 7. Direct skill-help questions: "Who knows Django?", "Who can help me with Django?", "Who is good at Python?"
        m_who_skill = re.match(
            r"^(?:who\s+knows|who\s+is\s+good\s+at|who\s+can\s+help\s+(?:me\s+)?with)\s+([^?\.]+?)[\s\?\.]*$",
            query,
            re.IGNORECASE,
        )
        if m_who_skill:
            raw_topic = m_who_skill.group(1).strip()
            topic, collab_status = self._extract_topic_and_status(raw_topic)
            # Guard against generic non-skill questions like "who can help me with my project" or "who can help me with homework"
            if topic and topic.lower() not in ("my project", "a project", "the project", "this project", "homework", "assignment"):
                params = {
                    "skills": [topic],
                    "interests": [topic],
                    "limit": 10,
                    "query": query,
                }
                if collab_status:
                    params["collaboration_status"] = collab_status
                return ToolRoute(
                    tool_name="people_discovery",
                    parameters=params,
                    confidence=0.95,
                    matched_intent="people_skill_help_discovery",
                )

        # 8. Standard people discovery with conversational prefixes:
        # "Find students interested in Python", "I need someone who knows Django", "Find someone who can help with Python",
        # "Find someone interested in AI who is open to projects"
        m_general = re.search(
            r"(?:refer\s+me\s+to|recommend(?:\s+me)?|find(?:\s+me)?|suggest|search\s+for|lookup|show\s+me|i\s+need|need|looking\s+for)?\s*(?:some\s+(?:of\s+the\s+)?)?"
            r"(?:students|people|peers|classmates|someone)\s+(?:interested\s+in|who\s+knows?|skilled\s+in|with\s+skills\s+in|who\s+are\s+interested\s+in|who\s+can\s+help\s+(?:me\s+)?with|good\s+at)\s+([^.]+?)[\s\?\.]*$",
            query,
            re.IGNORECASE,
        )
        if m_general:
            raw_topic = m_general.group(1).strip()
            topic, collab_status = self._extract_topic_and_status(raw_topic)
            if topic and topic.lower() not in ("my project", "a project", "homework"):
                params = {
                    "skills": [topic],
                    "interests": [topic],
                    "limit": 10,
                    "query": query,
                }
                if collab_status:
                    params["collaboration_status"] = collab_status
                return ToolRoute(
                    tool_name="people_discovery",
                    parameters=params,
                    confidence=0.95,
                    matched_intent="people_skill_discovery",
                )

        return None
