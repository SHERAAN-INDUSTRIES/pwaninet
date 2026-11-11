"""
Pwanimate Context Engine Service.

Transforms authorized retrieval results into a safe, deterministic,
bounded, and citation-preserving ContextPackage.
Operates purely in-memory with zero database queries.
"""

import hashlib
import logging
from typing import Any, Dict, List, Optional, Set

from pwanimate.retrieval.types import RetrievalResponse, RetrievalResult, SourceType
from pwanimate.context.types import (
    ContextRequest,
    ContextItem,
    ContextPackage,
    estimate_tokens,
    GroundingMode,
    IntentCategory,
    AuthorityLevel,
    StudentContext,
    ToolResult,
)

logger = logging.getLogger(__name__)


class ContextEngine:
    """
    Context Engine responsible for selection, deduplication, bounding,
    truncation, and citation preservation across retrieval results.
    """

    def build_student_context(
        self,
        user_context: Any,
        intent: IntentCategory,
        query: str
    ) -> Optional[StudentContext]:
        """
        Build selective student context based on query intent.
        
        Only includes relevant profile fields rather than dumping the entire profile.
        
        Args:
            user_context: Full UserContext from UserContextService.
            intent: Classified intent category.
            query: User query text for relevance analysis.
            
        Returns:
            StudentContext with only relevant fields, or None if no relevant fields.
        """
        if not user_context:
            return None
        
        query_lower = query.lower()
        
        # Determine which fields are relevant based on intent
        programme_name = None
        level = None
        school_name = None
        department_name = None
        semester = None
        enrolled_units: List[str] = []
        relevant_interests = []
        relevant_skills = []
        collaboration_status = ""
        nickname = ""
        tone = "neutral"
        response_style = "balanced"
        personal_instructions = ""
        
        # Always include preferences if available
        if hasattr(user_context, 'nickname'):
            nickname = user_context.nickname or ""
        if hasattr(user_context, 'tone'):
            tone = user_context.tone or "neutral"
        if hasattr(user_context, 'response_style'):
            response_style = user_context.response_style or "balanced"
        if hasattr(user_context, 'personal_instructions'):
            personal_instructions = user_context.personal_instructions or ""
        
        # Programme, level, school, semester, and enrolled units keep Pwanimate natively grounded in the student's academic context
        if intent in [
            IntentCategory.Vasto_KNOWLEDGE,
            IntentCategory.PERSONALIZED_DISCOVERY,
            IntentCategory.MIXED,
            IntentCategory.GENERAL_KNOWLEDGE,
        ]:
            if hasattr(user_context, 'programme_name') and user_context.programme_name:
                programme_name = user_context.programme_name
            if hasattr(user_context, 'academic_level_name') and user_context.academic_level_name:
                level = user_context.academic_level_name
            if hasattr(user_context, 'school_name') and user_context.school_name:
                school_name = user_context.school_name
            if hasattr(user_context, 'department_name') and user_context.department_name:
                department_name = user_context.department_name
            if hasattr(user_context, 'semester') and user_context.semester is not None:
                semester = user_context.semester
            if hasattr(user_context, 'enrolled_units') and user_context.enrolled_units:
                for u in user_context.enrolled_units:
                    if isinstance(u, dict):
                        code = u.get('code', '')
                        name = u.get('name', '')
                        label = f"{code} ({name})" if code and name else (code or name)
                        if label:
                            enrolled_units.append(label)
                    elif isinstance(u, str) and u.strip():
                        enrolled_units.append(u.strip())
        
        # Interests relevant for personalized discovery
        if intent == IntentCategory.PERSONALIZED_DISCOVERY:
            if hasattr(user_context, 'interests'):
                relevant_interests = list(user_context.interests) if user_context.interests else []
        
        # Skills relevant for peer discovery or technical queries
        if intent == IntentCategory.PERSONALIZED_DISCOVERY or any(
            kw in query_lower for kw in ['skill', 'project', 'collaborat', 'partner', 'team']
        ):
            if hasattr(user_context, 'skills'):
                relevant_skills = list(user_context.skills) if user_context.skills else []
        
        # Collaboration status relevant for peer discovery
        if intent == IntentCategory.PERSONALIZED_DISCOVERY or 'collaborat' in query_lower:
            if hasattr(user_context, 'collaboration_status'):
                collaboration_status = user_context.collaboration_status or ""

        # Weekly timetable for academic, schedule, revision, or study planning
        weekly_timetable = None
        timetable_keywords = [
            'timetable', 'schedule', 'class', 'lecture', 'revision',
            'free time', 'free slot', 'study slot', 'today', 'tomorrow',
            'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday',
            'sunday', 'plan', 'exam', 'session', 'when do i have'
        ]
        if (
            intent in [IntentCategory.Vasto_KNOWLEDGE, IntentCategory.MIXED, IntentCategory.GENERAL_KNOWLEDGE]
            or any(kw in query_lower for kw in timetable_keywords)
        ):
            if hasattr(user_context, 'weekly_timetable'):
                weekly_timetable = user_context.weekly_timetable

        # Build StudentContext if any relevant fields exist
        student_ctx = StudentContext(
            programme_name=programme_name,
            level=level,
            school_name=school_name,
            department_name=department_name,
            semester=semester,
            enrolled_units=enrolled_units,
            relevant_interests=relevant_interests,
            relevant_skills=relevant_skills,
            collaboration_status=collaboration_status,
            nickname=nickname,
            tone=tone,
            response_style=response_style,
            personal_instructions=personal_instructions,
            weekly_timetable=weekly_timetable,
        )
        
        return student_ctx if student_ctx.has_content() else None

    def select_conversation_history(
        self,
        conversation: List[Any],
        intent: IntentCategory,
        max_turns: int = 5
    ) -> List[Any]:
        """
        Select relevant conversation history based on intent.
        
        Prioritizes recent turns, but excludes purely conversational chitchat
        for substantive queries.
        
        Args:
            conversation: Full conversation history.
            intent: Classified intent category.
            max_turns: Maximum number of turns to include.
            
        Returns:
            Selected conversation turns.
        """
        if not conversation:
            return []
        
        # For conversational intent, include recent turns (most recent first)
        if intent == IntentCategory.CONVERSATIONAL:
            return list(reversed(conversation[-max_turns:])) if len(conversation) > max_turns else list(reversed(conversation))
        
        # For substantive queries, select recent substantive turns
        # Filter out very short greetings/acknowledgments
        selected = []
        for turn in reversed(conversation):
            if len(selected) >= max_turns:
                break
            
            # Skip purely conversational turns
            if hasattr(turn, 'content'):
                content = turn.content if isinstance(turn.content, str) else str(turn.content)
                if len(content.strip()) < 10:
                    continue
            
            selected.insert(0, turn)
        
        return selected

    def prioritize_explicit_resources(
        self,
        explicit_resources: List[ContextItem],
        retrieved_context: List[ContextItem],
        grounding_mode: GroundingMode
    ) -> tuple[List[ContextItem], List[ContextItem]]:
        """
        Prioritize explicit resources over retrieved context.
        
        In EXPLICIT_RESOURCE mode, explicit resources receive highest priority.
        Deduplicates between explicit and retrieved resources.
        
        Args:
            explicit_resources: User-selected resources.
            retrieved_context: Retrieved evidence from Vasto.
            grounding_mode: Current grounding mode.
            
        Returns:
            Tuple of (prioritized_explicit, filtered_retrieved).
        """
        if not explicit_resources:
            return [], retrieved_context
        
        # Mark explicit resources with highest authority
        for item in explicit_resources:
            item.explicitly_selected = True
            item.authority_level = AuthorityLevel.EXPLICIT
        
        # Deduplicate: remove retrieved items that are already in explicit resources
        explicit_ids = {(item.source.value if hasattr(item.source, 'value') else str(item.source), str(item.object_id)) for item in explicit_resources}
        
        filtered_retrieved = []
        for item in retrieved_context:
            item_key = (item.source.value if hasattr(item.source, 'value') else str(item.source), str(item.object_id))
            if item_key not in explicit_ids:
                filtered_retrieved.append(item)
        
        # In EXPLICIT_RESOURCE mode, retrieved context is supplementary
        if grounding_mode == GroundingMode.EXPLICIT_RESOURCE:
            return explicit_resources, filtered_retrieved[:3]  # Limit retrieved to top 3
        
        return explicit_resources, filtered_retrieved

    def enforce_source_hierarchy(
        self,
        items: List[ContextItem]
    ) -> List[ContextItem]:
        """
        Enforce source hierarchy in context item ordering.
        
        Priority order: EXPLICIT > PLATFORM > USER_GENERATED > PROFILE
        
        Args:
            items: Context items to reorder.
            
        Returns:
            Reordered items by authority level.
        """
        authority_order = {
            AuthorityLevel.EXPLICIT: 0,
            AuthorityLevel.PLATFORM: 1,
            AuthorityLevel.USER_GENERATED: 2,
            AuthorityLevel.PROFILE: 3,
        }
        
        # Sort by authority level, then by relevance score within same level
        def sort_key(item):
            authority_level = item.authority_level if isinstance(item.authority_level, AuthorityLevel) else AuthorityLevel.USER_GENERATED
            authority_priority = authority_order.get(authority_level, 2)
            return (authority_priority, -item.relevance_score)
        
        return sorted(items, key=sort_key)

    def build_context(self, request: ContextRequest) -> ContextPackage:
        """
        Build a grounded, bounded ContextPackage from a ContextRequest.

        Args:
            request: ContextRequest containing query, retrieval results, and budget parameters.

        Returns:
            ContextPackage ready for future AI Gateway consumption.
        """
        query = (request.query or "").strip()
        intent = request.intent if request.intent else IntentCategory.MIXED
        grounding_mode = request.grounding_mode if request.grounding_mode else GroundingMode.OPTIONAL
        
        # Build selective student context
        student_context = self.build_student_context(
            request.user_context,
            intent,
            query
        )
        
        # Select conversation history
        selected_conversation = self.select_conversation_history(
            request.conversation if request.conversation else [],
            intent
        )
        
        # Process explicit resources if provided
        explicit_resources = []
        if request.explicit_resources:
            explicit_resources = list(request.explicit_resources)
        
        # Process retrieved results
        raw_results = request.get_raw_results() if query else []
        budget_state = {}
        retrieved_items = []
        
        if raw_results:
            retrieved_items = self._process_retrieved_results(
                raw_results,
                request,
                grounding_mode,
                budget_state=budget_state,
            )
        
        # Prioritize explicit resources over retrieved context
        prioritized_explicit, filtered_retrieved = self.prioritize_explicit_resources(
            explicit_resources,
            retrieved_items,
            grounding_mode
        )
        
        # Enforce source hierarchy on combined context
        all_items = prioritized_explicit + filtered_retrieved
        ordered_items = self.enforce_source_hierarchy(all_items)
        
        # Calculate totals
        total_items = len(ordered_items)
        estimated_tokens = sum(item.estimated_tokens for item in ordered_items)
        total_characters = sum(len(item.content) for item in ordered_items)
        truncated = budget_state.get('truncated', False) or any(item.truncated for item in ordered_items)
        
        # Collect source counts
        source_counts = {}
        for item in ordered_items:
            src_str = item.source.value if hasattr(item.source, 'value') else str(item.source)
            source_counts[src_str] = source_counts.get(src_str, 0) + 1
        
        # Collect unique citations
        citations = []
        for item in ordered_items:
            if item.citation and item.citation not in citations:
                citations.append(item.citation)
        
        # Process tool results
        tool_results = list(request.tool_results) if request.tool_results else []
        
        # Build metadata
        metadata = {
            "retrieval_used": len(raw_results) > 0 if raw_results else False,
            "sources_used": list(source_counts.keys()),
            "token_budget": request.max_tokens,
            "context_version": "2.0",
            "intent": intent.value if isinstance(intent, IntentCategory) else str(intent),
            "grounding_mode": grounding_mode.value if isinstance(grounding_mode, GroundingMode) else str(grounding_mode),
        }
        
        # Return ContextPackage with new structure
        return ContextPackage(
            query=query,
            intent=intent,
            grounding_mode=grounding_mode,
            items=ordered_items,  # Legacy field for backward compatibility
            citations=citations,
            total_items=total_items,
            estimated_tokens=estimated_tokens,
            total_characters=total_characters,
            truncated=truncated,
            source_counts=source_counts,
            user_context=request.user_context,  # Legacy field
            student_context=student_context,
            explicit_resources=prioritized_explicit,
            retrieved_context=filtered_retrieved,
            tool_results=tool_results,
            conversation=selected_conversation,
            attachment_context=None,  # Attachment context handled separately in orchestrator
            metadata=metadata,
        )

    def _process_retrieved_results(
        self,
        raw_results: List[RetrievalResult],
        request: ContextRequest,
        grounding_mode: GroundingMode,
        budget_state: Optional[dict] = None,
    ) -> List[ContextItem]:
        """
        Process retrieved results into ContextItems with budgeting and deduplication.
        
        This is the core selection logic from the original build_context method,
        adapted to return a list of ContextItems.
        """
        selected_items: List[ContextItem] = []
        seen_objects: Set[tuple] = set()
        seen_hashes: Set[str] = set()
        doc_chunk_counts = {}
        source_counts = {}

        current_tokens = 0
        current_chars = 0
        package_truncated = False

        source_caps = request.max_results_per_source or {}

        for r in raw_results:
            # 1. Relevance threshold check
            if request.min_score > 0.0 and r.score < request.min_score:
                continue

            # 2. Source normalization
            src_type = r.source if isinstance(r.source, SourceType) else SourceType(str(r.source).lower().strip())
            src_name = src_type.value

            # 3. Source balancing quota check
            if src_name in source_caps and source_counts.get(src_name, 0) >= source_caps[src_name]:
                continue

            # 4. Exact object deduplication (e.g. post:123 encountered twice)
            obj_key = (src_name, str(r.object_id))
            if obj_key in seen_objects:
                continue

            # 5. Document chunk capping (prevent single doc from dominating all slots)
            doc_id = r.metadata.get("document_id") if src_type == SourceType.DOCUMENT else None
            if doc_id is not None:
                if doc_chunk_counts.get(doc_id, 0) >= request.max_chunks_per_document:
                    continue

            # 6. Extract and validate content
            raw_content = (r.snippet or "").strip()
            if not raw_content:
                continue

            # Content hash deduplication (detect exact duplicate text across chunks)
            content_hash = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
            if content_hash in seen_hashes:
                continue

            # 7. Item-level character budget truncation
            item_truncated = False
            content = raw_content
            if len(content) > request.max_characters_per_item:
                content = content[:request.max_characters_per_item].rstrip() + "... [TRUNCATED]"
                item_truncated = True
                package_truncated = True

            # 8. Check remaining package budget
            remaining_chars = request.max_characters - current_chars
            remaining_tokens = request.max_tokens - current_tokens

            # If remaining budget is exhausted or too small to be meaningful, stop selection
            if remaining_chars <= 100 or remaining_tokens <= 25:
                package_truncated = True
                break

            # If content exceeds remaining characters, truncate gracefully
            if len(content) > remaining_chars:
                cutoff = max(50, remaining_chars - 17)
                content = content[:cutoff].rstrip() + "... [TRUNCATED]"
                item_truncated = True
                package_truncated = True

            # Check estimated tokens
            item_tokens = estimate_tokens(content)
            if item_tokens > remaining_tokens:
                allowed_chars = max(50, remaining_tokens * 4 - 17)
                content = content[:allowed_chars].rstrip() + "... [TRUNCATED]"
                item_tokens = estimate_tokens(content)
                item_truncated = True
                package_truncated = True

            # 9. Determine authority level based on source type
            authority_level = AuthorityLevel.USER_GENERATED
            if src_type == SourceType.DOCUMENT:
                authority_level = AuthorityLevel.PLATFORM
            elif src_type == SourceType.USER:
                authority_level = AuthorityLevel.PROFILE

            # 10. Build citation metadata
            citation_metadata = {}
            if r.metadata:
                citation_metadata = {
                    "document_id": r.metadata.get("document_id"),
                    "page_number": r.metadata.get("page_number"),
                    "slide_number": r.metadata.get("slide_number"),
                    "section_heading": r.metadata.get("section_heading"),
                }

            # 11. Construct normalized ContextItem
            item = ContextItem(
                source=src_type,
                object_id=r.object_id,
                title=r.title,
                content=content,
                citation=r.citation,
                url=r.url,
                relevance_score=r.score,
                metadata=r.metadata.copy(),
                estimated_tokens=item_tokens,
                truncated=item_truncated,
                explicitly_selected=False,
                authority_level=authority_level,
                citation_metadata=citation_metadata,
            )

            # 12. Register tracking state
            seen_objects.add(obj_key)
            seen_hashes.add(content_hash)
            if doc_id is not None:
                doc_chunk_counts[doc_id] = doc_chunk_counts.get(doc_id, 0) + 1
            source_counts[src_name] = source_counts.get(src_name, 0) + 1

            current_tokens += item_tokens
            current_chars += len(content)
            selected_items.append(item)

            # 13. Max results limit
            if len(selected_items) >= request.max_results:
                break

        if budget_state is not None:
            budget_state['truncated'] = package_truncated
        return selected_items

    def build_from_response(
        self,
        query: str,
        retrieval_response: RetrievalResponse,
        user_context: Optional[Any] = None,
        intent: Optional[IntentCategory] = None,
        grounding_mode: Optional[GroundingMode] = None,
        **kwargs
    ) -> ContextPackage:
        """Convenience method to construct context directly from a RetrievalResponse."""
        request = ContextRequest(
            query=query,
            retrieval_response=retrieval_response,
            user_context=user_context,
            intent=intent or IntentCategory.MIXED,
            grounding_mode=grounding_mode or GroundingMode.OPTIONAL,
            **kwargs
        )
        return self.build_context(request)

    def log_context_build(
        self,
        request: ContextRequest,
        package: ContextPackage
    ) -> None:
        """
        Log context build metrics for debugging and monitoring.
        
        Args:
            request: The ContextRequest that was processed.
            package: The resulting ContextPackage.
        """
        logger.info(
            "ContextEngine build: query='%s' intent=%s grounding_mode=%s "
            "retrieval_used=%s explicit_resources=%d retrieved_context=%d "
            "conversation=%d total_items=%d estimated_tokens=%d truncated=%s",
            request.query[:50] + "..." if len(request.query) > 50 else request.query,
            package.intent.value if isinstance(package.intent, IntentCategory) else str(package.intent),
            package.grounding_mode.value if isinstance(package.grounding_mode, GroundingMode) else str(package.grounding_mode),
            package.metadata.get("retrieval_used", False),
            len(package.explicit_resources),
            len(package.retrieved_context),
            len(package.conversation),
            package.total_items,
            package.estimated_tokens,
            package.truncated,
        )
