"""
Context Engine Phase 2 Test Suite.

Tests the new ContextEngine methods:
- build_student_context (selective profile inclusion)
- select_conversation_history (conversation selection)
- prioritize_explicit_resources (explicit resource prioritization)
- enforce_source_hierarchy (source hierarchy enforcement)
- Enhanced build_context with new fields
"""

from unittest.mock import MagicMock
from django.test import SimpleTestCase

from pwanimate.context import (
    ContextEngine,
    ContextRequest,
    ContextItem,
    ContextPackage,
    GroundingMode,
    IntentCategory,
    AuthorityLevel,
    StudentContext,
    ToolResult,
)
from pwanimate.retrieval.types import RetrievalResponse, RetrievalResult, SourceType


class BuildStudentContextTestCase(SimpleTestCase):
    """Test selective student context building."""

    def setUp(self):
        self.engine = ContextEngine()
        self.mock_user_context = MagicMock()
        self.mock_user_context.nickname = "Dev"
        self.mock_user_context.tone = "friendly"
        self.mock_user_context.response_style = "concise"
        self.mock_user_context.programme_name = "Computer Science"
        self.mock_user_context.academic_level_name = "Year 2"
        self.mock_user_context.interests = ["AI", "Machine Learning"]
        self.mock_user_context.skills = ["Python", "Django"]
        self.mock_user_context.collaboration_status = "open_to_projects"

    def test_student_context_for_conversational_intent(self):
        """Conversational intent should only include preferences."""
        student_ctx = self.engine.build_student_context(
            self.mock_user_context,
            IntentCategory.CONVERSATIONAL,
            "Hello there!"
        )
        
        self.assertIsNotNone(student_ctx)
        self.assertEqual(student_ctx.nickname, "Dev")
        self.assertEqual(student_ctx.tone, "friendly")
        self.assertEqual(student_ctx.response_style, "concise")
        # Programme/level should not be included for conversational
        self.assertIsNone(student_ctx.programme_name)
        self.assertIsNone(student_ctx.level)

    def test_student_context_for_personalized_discovery(self):
        """Personalized discovery should include interests and skills."""
        student_ctx = self.engine.build_student_context(
            self.mock_user_context,
            IntentCategory.PERSONALIZED_DISCOVERY,
            "Find me collaborators"
        )
        
        self.assertIsNotNone(student_ctx)
        self.assertEqual(student_ctx.programme_name, "Computer Science")
        self.assertEqual(student_ctx.level, "Year 2")
        self.assertEqual(len(student_ctx.relevant_interests), 2)
        self.assertEqual(len(student_ctx.relevant_skills), 2)
        self.assertEqual(student_ctx.collaboration_status, "open_to_projects")

    def test_student_context_for_general_knowledge(self):
        """General knowledge retains academic grounding alongside preferences."""
        student_ctx = self.engine.build_student_context(
            self.mock_user_context,
            IntentCategory.GENERAL_KNOWLEDGE,
            "What is recursion?"
        )
        
        self.assertIsNotNone(student_ctx)
        self.assertEqual(student_ctx.nickname, "Dev")
        self.assertEqual(student_ctx.programme_name, 'Computer Science')
        self.assertEqual(len(student_ctx.relevant_interests), 0)
        self.assertEqual(len(student_ctx.relevant_skills), 0)

    def test_student_context_for_vasto_knowledge(self):
        """vasto knowledge should include programme/level."""
        student_ctx = self.engine.build_student_context(
            self.mock_user_context,
            IntentCategory.Vasto_KNOWLEDGE,
            "What are the CS prerequisites?"
        )
        
        self.assertIsNotNone(student_ctx)
        self.assertEqual(student_ctx.programme_name, "Computer Science")
        self.assertEqual(student_ctx.level, "Year 2")

    def test_student_context_with_none_user_context(self):
        """None user context should return None."""
        student_ctx = self.engine.build_student_context(
            None,
            IntentCategory.MIXED,
            "Test query"
        )
        
        self.assertIsNone(student_ctx)

    def test_student_context_query_keyword_matching(self):
        """Query keywords should trigger relevant field inclusion."""
        # Query with "collaborat" should include collaboration status
        student_ctx = self.engine.build_student_context(
            self.mock_user_context,
            IntentCategory.MIXED,
            "I want to collaborate on a project"
        )
        
        self.assertIsNotNone(student_ctx)
        self.assertEqual(student_ctx.collaboration_status, "open_to_projects")


class SelectConversationHistoryTestCase(SimpleTestCase):
    """Test conversation history selection."""

    def setUp(self):
        self.engine = ContextEngine()
        self.mock_conversation = [
            MagicMock(content="Hello", role="user"),
            MagicMock(content="Hi there!", role="assistant"),
            MagicMock(content="What is Python?", role="user"),
            MagicMock(content="Python is a programming language.", role="assistant"),
            MagicMock(content="Thanks", role="user"),
            MagicMock(content="You're welcome!", role="assistant"),
        ]

    def test_conversational_intent_includes_recent_turns(self):
        """Conversational intent should include recent turns."""
        selected = self.engine.select_conversation_history(
            self.mock_conversation,
            IntentCategory.CONVERSATIONAL,
            max_turns=3
        )
        
        self.assertEqual(len(selected), 3)
        # Should be the last 3 turns (most recent first due to reverse)
        self.assertEqual(selected[0].content, "You're welcome!")
        self.assertEqual(selected[1].content, "Thanks")
        self.assertEqual(selected[2].content, "Python is a programming language.")

    def test_substantive_intent_filters_short_turns(self):
        """Substantive intent should filter out short greetings."""
        selected = self.engine.select_conversation_history(
            self.mock_conversation,
            IntentCategory.GENERAL_KNOWLEDGE,
            max_turns=5
        )
        
        # Should exclude "Hello", "Hi there!", "Thanks", "You're welcome!"
        # Only include substantive turns
        self.assertLess(len(selected), len(self.mock_conversation))
        # Should include the substantive Q&A
        substantive_contents = [turn.content for turn in selected]
        self.assertIn("What is Python?", substantive_contents)
        self.assertIn("Python is a programming language.", substantive_contents)

    def test_empty_conversation_returns_empty(self):
        """Empty conversation should return empty list."""
        selected = self.engine.select_conversation_history(
            [],
            IntentCategory.MIXED
        )
        
        self.assertEqual(selected, [])

    def test_max_turns_limit(self):
        """max_turns should limit the number of returned turns."""
        selected = self.engine.select_conversation_history(
            self.mock_conversation,
            IntentCategory.CONVERSATIONAL,
            max_turns=2
        )
        
        self.assertEqual(len(selected), 2)


class PrioritizeExplicitResourcesTestCase(SimpleTestCase):
    """Test explicit resource prioritization."""

    def setUp(self):
        self.engine = ContextEngine()
        
        self.explicit_item = ContextItem(
            source="document",
            object_id=1,
            title="Explicit Doc",
            content="Explicit content",
            explicitly_selected=False,  # Will be set to True by method
        )
        
        self.retrieved_item = ContextItem(
            source="document",
            object_id=2,
            title="Retrieved Doc",
            content="Retrieved content",
        )
        
        self.duplicate_item = ContextItem(
            source="document",
            object_id=1,
            title="Duplicate Doc",
            content="Duplicate content",
        )

    def test_explicit_resources_marked_as_selected(self):
        """Explicit resources should be marked as explicitly_selected."""
        explicit, retrieved = self.engine.prioritize_explicit_resources(
            [self.explicit_item],
            [self.retrieved_item],
            GroundingMode.EXPLICIT_RESOURCE
        )
        
        self.assertEqual(len(explicit), 1)
        self.assertTrue(explicit[0].explicitly_selected)
        self.assertEqual(explicit[0].authority_level, AuthorityLevel.EXPLICIT)

    def test_deduplication_removes_duplicates_from_retrieved(self):
        """Duplicate items should be removed from retrieved context."""
        explicit, retrieved = self.engine.prioritize_explicit_resources(
            [self.explicit_item],
            [self.retrieved_item, self.duplicate_item],
            GroundingMode.OPTIONAL
        )
        
        self.assertEqual(len(explicit), 1)
        # Only one retrieved item should remain (duplicate removed)
        self.assertEqual(len(retrieved), 1)

    def test_explicit_resource_mode_limits_retrieved(self):
        """EXPLICIT_RESOURCE mode should limit retrieved context."""
        explicit, retrieved = self.engine.prioritize_explicit_resources(
            [self.explicit_item],
            [self.retrieved_item, self.duplicate_item],
            GroundingMode.EXPLICIT_RESOURCE
        )
        
        self.assertEqual(len(explicit), 1)
        # Retrieved should be limited to top 3 (in this case, only 1 after dedup)
        self.assertLessEqual(len(retrieved), 3)

    def test_empty_explicit_resources(self):
        """Empty explicit resources should return all retrieved."""
        explicit, retrieved = self.engine.prioritize_explicit_resources(
            [],
            [self.retrieved_item, self.duplicate_item],
            GroundingMode.OPTIONAL
        )
        
        self.assertEqual(len(explicit), 0)
        self.assertEqual(len(retrieved), 2)


class EnforceSourceHierarchyTestCase(SimpleTestCase):
    """Test source hierarchy enforcement."""

    def setUp(self):
        self.engine = ContextEngine()
        
        self.items = [
            ContextItem(
                source="post",
                object_id=1,
                title="User Post",
                content="User content",
                authority_level=AuthorityLevel.USER_GENERATED,
                relevance_score=0.9,
            ),
            ContextItem(
                source="document",
                object_id=2,
                title="Platform Doc",
                content="Platform content",
                authority_level=AuthorityLevel.PLATFORM,
                relevance_score=0.7,
            ),
            ContextItem(
                source="document",
                object_id=3,
                title="Explicit Doc",
                content="Explicit content",
                authority_level=AuthorityLevel.EXPLICIT,
                relevance_score=0.5,
            ),
            ContextItem(
                source="user",
                object_id=4,
                title="Profile",
                content="Profile content",
                authority_level=AuthorityLevel.PROFILE,
                relevance_score=0.8,
            ),
        ]

    def test_hierarchy_ordering(self):
        """Items should be ordered by authority level."""
        ordered = self.engine.enforce_source_hierarchy(self.items)
        
        # Order should be: EXPLICIT > PLATFORM > USER_GENERATED > PROFILE
        self.assertEqual(ordered[0].authority_level, AuthorityLevel.EXPLICIT)
        self.assertEqual(ordered[1].authority_level, AuthorityLevel.PLATFORM)
        self.assertEqual(ordered[2].authority_level, AuthorityLevel.USER_GENERATED)
        self.assertEqual(ordered[3].authority_level, AuthorityLevel.PROFILE)

    def test_same_authority_sorted_by_relevance(self):
        """Items with same authority should be sorted by relevance (descending)."""
        items = [
            ContextItem(
                source="post",
                object_id=1,
                title="Post 1",
                content="Content 1",
                authority_level=AuthorityLevel.USER_GENERATED,
                relevance_score=0.5,
            ),
            ContextItem(
                source="post",
                object_id=2,
                title="Post 2",
                content="Content 2",
                authority_level=AuthorityLevel.USER_GENERATED,
                relevance_score=0.9,
            ),
        ]
        
        ordered = self.engine.enforce_source_hierarchy(items)
        
        self.assertEqual(ordered[0].relevance_score, 0.9)
        self.assertEqual(ordered[1].relevance_score, 0.5)


class EnhancedBuildContextTestCase(SimpleTestCase):
    """Test enhanced build_context with new fields."""

    def setUp(self):
        self.engine = ContextEngine()
        
        self.mock_retrieval_result = RetrievalResult(
            source=SourceType.DOCUMENT,
            object_id=1,
            title="Test Document",
            snippet="Test content",
            score=0.9,
            url="/doc/1/",
            citation="[Test, v1: p. 1]",
        )
        
        self.mock_retrieval_response = RetrievalResponse(
            query="test query",
            results=[self.mock_retrieval_result],
            total_count=1,
            execution_time_ms=5.0,
        )

    def test_build_context_with_intent_and_grounding_mode(self):
        """build_context should use intent and grounding_mode from request."""
        request = ContextRequest(
            query="test query",
            intent=IntentCategory.GENERAL_KNOWLEDGE,
            grounding_mode=GroundingMode.NONE,
            retrieval_response=self.mock_retrieval_response,
        )
        
        package = self.engine.build_context(request)
        
        self.assertEqual(package.intent, IntentCategory.GENERAL_KNOWLEDGE)
        self.assertEqual(package.grounding_mode, GroundingMode.NONE)

    def test_build_context_creates_student_context(self):
        """build_context should create selective student context."""
        mock_user_context = MagicMock()
        mock_user_context.nickname = "Test"
        mock_user_context.tone = "neutral"
        mock_user_context.response_style = "balanced"
        
        request = ContextRequest(
            query="test query",
            intent=IntentCategory.PERSONALIZED_DISCOVERY,
            user_context=mock_user_context,
            retrieval_response=self.mock_retrieval_response,
        )
        
        package = self.engine.build_context(request)
        
        self.assertIsNotNone(package.student_context)
        self.assertEqual(package.student_context.nickname, "Test")

    def test_build_context_selects_conversation(self):
        """build_context should select conversation history."""
        mock_conversation = [
            MagicMock(content="Substantive question", role="user"),
            MagicMock(content="Substantive answer", role="assistant"),
        ]
        
        request = ContextRequest(
            query="test query",
            conversation=mock_conversation,
            retrieval_response=self.mock_retrieval_response,
        )
        
        package = self.engine.build_context(request)
        
        self.assertEqual(len(package.conversation), 2)

    def test_build_context_includes_tool_results(self):
        """build_context should include tool results."""
        tool_result = ToolResult(
            tool_name="test_tool",
            success=True,
            data="test_data",
        )
        
        request = ContextRequest(
            query="test query",
            tool_results=[tool_result],
            retrieval_response=self.mock_retrieval_response,
        )
        
        package = self.engine.build_context(request)
        
        self.assertEqual(len(package.tool_results), 1)
        self.assertEqual(package.tool_results[0].tool_name, "test_tool")

    def test_build_context_creates_metadata(self):
        """build_context should create metadata with intent and grounding_mode."""
        request = ContextRequest(
            query="test query",
            intent=IntentCategory.DOCUMENT_ANALYSIS,
            grounding_mode=GroundingMode.EXPLICIT_RESOURCE,
            retrieval_response=self.mock_retrieval_response,
        )
        
        package = self.engine.build_context(request)
        
        self.assertIn("intent", package.metadata)
        self.assertIn("grounding_mode", package.metadata)
        self.assertIn("retrieval_used", package.metadata)
        self.assertIn("sources_used", package.metadata)
        self.assertIn("token_budget", package.metadata)
        self.assertIn("context_version", package.metadata)

    def test_build_context_backward_compatibility(self):
        """build_context should maintain backward compatibility with legacy fields."""
        request = ContextRequest(
            query="test query",
            retrieval_response=self.mock_retrieval_response,
            user_context="test user context",  # Provide user context
        )
        
        package = self.engine.build_context(request)
        
        # Legacy fields should still be populated
        self.assertEqual(len(package.items), 1)
        self.assertEqual(package.user_context, "test user context")

    def test_build_context_with_explicit_resources(self):
        """build_context should handle explicit resources."""
        explicit_item = ContextItem(
            source="document",
            object_id=10,
            title="Explicit",
            content="Explicit content",
        )
        
        request = ContextRequest(
            query="test query",
            explicit_resources=[explicit_item],
            retrieval_response=self.mock_retrieval_response,
        )
        
        package = self.engine.build_context(request)
        
        self.assertEqual(len(package.explicit_resources), 1)
        self.assertTrue(package.explicit_resources[0].explicitly_selected)

    def test_build_context_separates_retrieved_context(self):
        """build_context should separate retrieved context from items."""
        request = ContextRequest(
            query="test query",
            retrieval_response=self.mock_retrieval_response,
        )
        
        package = self.engine.build_context(request)
        
        # Both items and retrieved_context should be populated
        self.assertEqual(len(package.items), 1)
        self.assertEqual(len(package.retrieved_context), 1)


class LogContextBuildTestCase(SimpleTestCase):
    """Test context build logging."""

    def setUp(self):
        self.engine = ContextEngine()

    def test_log_context_build(self):
        """log_context_build should log context metrics."""
        request = ContextRequest(
            query="test query",
            intent=IntentCategory.GENERAL_KNOWLEDGE,
            grounding_mode=GroundingMode.NONE,
        )
        
        package = ContextPackage(
            query="test query",
            intent=IntentCategory.GENERAL_KNOWLEDGE,
            grounding_mode=GroundingMode.NONE,
            explicit_resources=[],
            retrieved_context=[],
            metadata={
                "retrieval_used": False,
                "intent": "general_knowledge",
                "grounding_mode": "none",
            },
        )
        
        # This should not raise an exception
        self.engine.log_context_build(request, package)
