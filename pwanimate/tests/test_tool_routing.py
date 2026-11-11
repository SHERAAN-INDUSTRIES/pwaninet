"""
Comprehensive Test Suite for Pwanimate Phase 8D: Deterministic Tool Routing & Orchestrator Integration.

Tests:
1. ToolRouter (high-confidence pattern matching for all 5 tools, negative / false-positive rejection).
2. Tool Context Adapter (translation of ToolResults to ContextItems and ContextPackages).
3. Orchestrator Tool Integration (end-to-end routing, single-pass LLM grounding, citation preservation).
4. Fallback Semantics (conversational bypass, non-tool RAG fallback, permission boundary enforcement).
5. Architectural Invariants (zero loopback HTTP, AI Gateway contract preservation).
"""

import uuid
from unittest.mock import patch
from django.test import TestCase, SimpleTestCase
from django.contrib.auth import get_user_model

from courses.models import School, Programme, OfficialSchoolCode, AcademicLevelType
from documents.models import Document, Category
from groups.models import Group, Membership, MembershipStatus, PostVisibility
from posts.models import Post
from notifications.models import NotificationObject
from users.models import PrivacyLevel

from pwanimate.ai.gateway import AIGateway, LLMRouter
from pwanimate.ai.providers import MockLLMProvider
from pwanimate.context.types import ContextPackage, ContextItem
from pwanimate.orchestrator import OrchestrationRequest, PwanimateOrchestrator
from pwanimate.tools import (
    ToolRoute,
    ToolRouter,
    ToolRegistry,
    get_default_tool_registry,
    tool_result_to_context_items,
    build_tool_context_package,
)

User = get_user_model()


class ToolRouterTestCase(SimpleTestCase):
    """Test ToolRouter deterministic pattern matching and false-positive protection."""

    def setUp(self):
        self.router = ToolRouter()

    # --- 1. Notification Routing ---
    def test_route_notifications_positive(self):
        queries = [
            "my notifications",
            "my unread notifications",
            "check my notifications",
            "what are my unread notifications?",
            "do I have notifications",
            "do I have any new notifications?",
            "how many unread notifications do I have?",
            "what notifications do I have",
        ]
        for q in queries:
            route = self.router.route(q)
            self.assertIsNotNone(route, f"Failed to match notification query: '{q}'")
            self.assertEqual(route.tool_name, "notification_summary")
            self.assertTrue(route.parameters.get("unread_only"))

    def test_route_notifications_all(self):
        route = self.router.route("show me all my notifications")
        self.assertIsNotNone(route)
        self.assertEqual(route.tool_name, "notification_summary")
        self.assertFalse(route.parameters.get("unread_only"))

    def test_route_notifications_negative(self):
        negatives = [
            "how do notifications work in vasto?",
            "why did notifications fail to send?",
            "create a notification for the students",
        ]
        for q in negatives:
            route = self.router.route(q)
            self.assertIsNone(route, f"False positive on notification query: '{q}'")

    # --- 2. User Profile Routing ---
    def test_route_user_profile_positive(self):
        queries = [
            ("who is @alice", "alice"),
            ("tell me about @Bob_Jones", "bob_jones"),
            ("show me @charlie-dev", "charlie-dev"),
            ("lookup @david.smith", "david.smith"),
            ("profile of @eve", "eve"),
            ("@frank", "frank"),
        ]
        for q, expected_username in queries:
            route = self.router.route(q)
            self.assertIsNotNone(route, f"Failed to match user query: '{q}'")
            self.assertEqual(route.tool_name, "user_profile")
            self.assertEqual(route.parameters.get("username"), expected_username)

    def test_route_user_profile_negative(self):
        # Ambiguous names without @ must NOT route to tool (avoids guessing usernames)
        negatives = [
            "tell me about John",
            "who is the dean of pure sciences?",
            "profile of me",
            "profile of system",
            "find users who study computer science",
        ]
        for q in negatives:
            route = self.router.route(q)
            self.assertIsNone(route, f"False positive on ambiguous user query: '{q}'")

    # --- 3. Document Detail Routing ---
    def test_route_document_detail_positive_uuid(self):
        doc_uuid = str(uuid.uuid4())
        queries = [
            f"tell me about document {doc_uuid}",
            f"details for document {doc_uuid}",
            f"what is in document {doc_uuid}?",
        ]
        for q in queries:
            route = self.router.route(q)
            self.assertIsNotNone(route, f"Failed to match doc UUID query: '{q}'")
            self.assertEqual(route.tool_name, "document_detail")
            self.assertEqual(route.parameters.get("share_id"), doc_uuid)

    def test_route_document_detail_positive_slug(self):
        queries = [
            ("details for document cs101-lecture-notes", "cs101-lecture-notes"),
            ("tell me about document operating-systems-midterm", "operating-systems-midterm"),
            ("what is in document linear-algebra-past-papers?", "linear-algebra-past-papers"),
        ]
        for q, expected_slug in queries:
            route = self.router.route(q)
            self.assertIsNotNone(route, f"Failed to match doc slug query: '{q}'")
            self.assertEqual(route.tool_name, "document_detail")
            self.assertEqual(route.parameters.get("slug"), expected_slug)

    def test_route_document_detail_negative(self):
        # Broad document searches must NOT route to document_detail
        negatives = [
            "find documents about operating systems",
            "documents for second year computer science",
            "what documents are available for linear algebra?",
            "search for past papers",
        ]
        for q in negatives:
            route = self.router.route(q)
            self.assertTrue(route is None or route.tool_name != "document_detail", f"False positive on broad document query: '{q}'")

    # --- 4. Group Announcements Routing ---
    def test_route_group_announcements_positive(self):
        queries = [
            ("announcements in Computer Science group", "Computer Science"),
            ("what announcements are in Chess Club?", "Chess"),
            ("what was posted in Tech Innovators group", "Tech Innovators"),
            ("latest posts in Pwani Developers club", "Pwani Developers"),
        ]
        for q, expected_group in queries:
            route = self.router.route(q)
            self.assertIsNotNone(route, f"Failed to match group announcement query: '{q}'")
            self.assertEqual(route.tool_name, "group_announcements")
            self.assertEqual(route.parameters.get("group_name"), expected_group)

    def test_route_group_announcements_negative(self):
        negatives = [
            "tell me about the CS group",
            "how do I join a group?",
            "find groups related to machine learning",
            "who created the sports group?",
        ]
        for q in negatives:
            route = self.router.route(q)
            self.assertIsNone(route, f"False positive on general group query: '{q}'")

    # --- 5. Academic Lookup Routing ---
    def test_route_academic_lookup_positive(self):
        queries = [
            ("what is BSC-CS", "BSC-CS"),
            ("tell me about SPAS", "SPAS"),
            ("lookup SEDU", "SEDU"),
            ("what is BED-SCI", "BED-SCI"),
        ]
        for q, expected_code in queries:
            route = self.router.route(q)
            self.assertIsNotNone(route, f"Failed to match academic code query: '{q}'")
            self.assertEqual(route.tool_name, "academic_lookup")
            self.assertEqual(route.parameters.get("code"), expected_code)

    def test_route_academic_structure_positive(self):
        route = self.router.route("structure of Computer Science programme")
        self.assertIsNotNone(route)
        self.assertEqual(route.tool_name, "academic_lookup")
        self.assertEqual(route.parameters.get("query"), "Computer Science")
        self.assertEqual(route.parameters.get("entity_type"), "programme")

    def test_route_academic_lookup_negative(self):
        negatives = [
            "what should I study next semester?",
            "how hard is computer science?",
            "explain calculus 1",
        ]
        for q in negatives:
            route = self.router.route(q)
            self.assertIsNone(route, f"False positive on generic query: '{q}'")

    # --- 6. Phase 5: People Discovery Routing ---

    def test_route_people_collaboration_positive(self):
        queries = [
            ("I need a project partner.", "open_to_projects", None),
            ("Find me a project partner.", "open_to_projects", None),
            ("I'm looking for a project partner.", "open_to_projects", None),
            ("Find someone to work with on my project.", "open_to_projects", None),
            ("I need a collaborator.", "open_to_projects", None),
            ("Find me a collaborator.", "open_to_projects", None),
            ("Who can I work with?", "open_to_projects", None),
            ("Find someone to work with.", "open_to_projects", None),
            ("Who is open to project work?", "open_to_projects", None),
            ("Who is open to working on projects?", "open_to_projects", None),
            ("Find students open to collaboration.", "any_open", None),
            ("I need a Django partner.", "open_to_projects", "Django"),
            ("Find a Django project partner.", "open_to_projects", "Django"),
        ]
        for q, expected_status, expected_skill in queries:
            route = self.router.route(q)
            self.assertIsNotNone(route, f"Failed to match collaboration query: '{q}'")
            self.assertEqual(route.tool_name, "people_discovery")
            self.assertEqual(
                route.parameters.get("collaboration_status"),
                expected_status,
                f"Wrong collaboration_status for '{q}': got {route.parameters.get('collaboration_status')}",
            )
            if expected_skill:
                self.assertIn(
                    expected_skill,
                    route.parameters.get("skills", []),
                    f"Expected skill '{expected_skill}' in parameters for '{q}'",
                )

    def test_route_people_study_positive(self):
        queries = [
            ("I'm looking for a study buddy.", None),
            ("Find me a study buddy.", None),
            ("Who is available for study groups?", None),
            ("Who is open to study groups?", None),
            ("Find someone to study with.", None),
            ("Find a study buddy interested in AI.", "AI"),
        ]
        for q, expected_topic in queries:
            route = self.router.route(q)
            self.assertIsNotNone(route, f"Failed to match study query: '{q}'")
            self.assertEqual(route.tool_name, "people_discovery")
            self.assertEqual(
                route.parameters.get("collaboration_status"),
                "open_to_study_groups",
                f"Expected 'open_to_study_groups' for '{q}'",
            )
            if expected_topic:
                self.assertIn(
                    expected_topic,
                    route.parameters.get("skills", []),
                    f"Expected topic '{expected_topic}' in skills for '{q}'",
                )

    def test_route_people_skill_help_positive(self):
        queries = [
            ("Find students who know Django.", "Django"),
            ("Who knows Django?", "Django"),
            ("Who can help me with Django?", "Django"),
            ("I need someone who knows Django.", "Django"),
            ("Find someone who can help with Python.", "Python"),
            ("Find someone good at Python.", "Python"),
        ]
        for q, expected_skill in queries:
            route = self.router.route(q)
            self.assertIsNotNone(route, f"Failed to match skill help query: '{q}'")
            self.assertEqual(route.tool_name, "people_discovery")
            self.assertIn(
                expected_skill,
                route.parameters.get("skills", []),
                f"Expected skill '{expected_skill}' in parameters for '{q}'",
            )

    def test_route_people_academic_and_scope_positive(self):
        # Bare viewer-relative scope without topic
        route = self.router.route("Find people in my programme.")
        self.assertIsNotNone(route)
        self.assertEqual(route.tool_name, "people_discovery")
        self.assertEqual(route.parameters.get("academic_scope"), "my_programme")

        # Scope + skill
        route = self.router.route("Find students in my programme who know Python.")
        self.assertIsNotNone(route)
        self.assertEqual(route.tool_name, "people_discovery")
        self.assertEqual(route.parameters.get("academic_scope"), "my_programme")
        self.assertIn("Python", route.parameters.get("skills", []))

        # Academic scope + skill
        route = self.router.route("Find CS students who know Django.")
        self.assertIsNotNone(route)
        self.assertEqual(route.tool_name, "people_discovery")
        self.assertEqual(route.parameters.get("academic_scope"), "CS")
        self.assertIn("Django", route.parameters.get("skills", []))

        # Relative scope + interest
        route = self.router.route("Find people in my programme interested in AI.")
        self.assertIsNotNone(route)
        self.assertEqual(route.tool_name, "people_discovery")
        self.assertEqual(route.parameters.get("academic_scope"), "my_programme")
        self.assertIn("AI", route.parameters.get("interests", []))

        # Someone in my programme with contraction
        route = self.router.route("Find someone in my programme who's interested in AI.")
        self.assertIsNotNone(route)
        self.assertEqual(route.tool_name, "people_discovery")
        self.assertEqual(route.parameters.get("academic_scope"), "my_programme")
        self.assertIn("AI", route.parameters.get("interests", []))

        # Someone + interest + collaboration
        route = self.router.route("Find someone interested in AI who is open to projects.")
        self.assertIsNotNone(route)
        self.assertEqual(route.tool_name, "people_discovery")
        self.assertIn("AI", route.parameters.get("skills", []))
        self.assertEqual(route.parameters.get("collaboration_status"), "open_to_projects")

    def test_route_people_discovery_negative_regressions(self):
        """Verify that content, document, academic, and non-person queries do not route to people_discovery."""
        negatives = [
            "Find Django notes.",
            "Find materials about normalization.",
            "Show me documents about Python.",
            "I need help understanding Django.",
            "Explain Django to me.",
            "What is normalization?",
            "Help me understand my project.",
            "What units are in second year?",
            "What courses does Computer Science have?",
            "What did my group announce?",
        ]
        for q in negatives:
            route = self.router.route(q)
            if route is not None:
                self.assertNotEqual(
                    route.tool_name,
                    "people_discovery",
                    f"False positive: '{q}' should NOT route to people_discovery (got intent: {route.matched_intent})",
                )


class ToolContextAdapterTestCase(SimpleTestCase):
    """Test converting ToolResults to ContextItems and assembling ContextPackages."""

    def test_academic_lookup_to_context_items(self):
        from pwanimate.tools.base import ToolResult
        tool_data = [
            {
                "entity_type": "programme",
                "id": 1,
                "code": "BSC-CS",
                "name": "BSc Computer Science",
                "slug": "bsc-cs",
                "academic_level": "Undergraduate",
                "duration_years": 4,
                "school_name": "School of Pure and Applied Sciences",
                "department_name": "Computing",
                "description": "Four-year computing degree",
            }
        ]
        result = ToolResult.ok(tool_data)
        items = tool_result_to_context_items("academic_lookup", result)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.source, "academic")
        self.assertEqual(item.citation, "[BSC-CS]")
        self.assertEqual(item.url, "/academic/programme/bsc-cs/")
        self.assertIn("Four-year computing degree", item.content)

    def test_document_detail_to_context_items(self):
        from pwanimate.tools.base import ToolResult
        tool_data = {
            "share_id": "11111111-2222-3333-4444-555555555555",
            "slug": "intro-algorithms",
            "title": "Introduction to Algorithms",
            "description": "Lecture slides for week 1",
            "category": "Lecture Notes",
            "view_count": 42,
            "download_count": 10,
            "canonical_url": "/documents/document/11111111-2222-3333-4444-555555555555/",
        }
        result = ToolResult.ok(tool_data)
        items = tool_result_to_context_items("document_detail", result)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.source, "document")
        self.assertEqual(item.citation, "[Introduction to Algorithms]")
        self.assertEqual(item.url, "/documents/document/11111111-2222-3333-4444-555555555555/")
        self.assertIn("42 views", item.content)

    def test_notification_summary_to_context_items(self):
        from pwanimate.tools.base import ToolResult
        tool_data = {
            "unread_count": 3,
            "count": 1,
            "notifications": [
                {
                    "type": "SYSTEM",
                    "title": "Orientation",
                    "summary": "Orientation starts at 9am",
                    "status": "CREATED",
                }
            ],
        }
        result = ToolResult.ok(tool_data)
        items = tool_result_to_context_items("notification_summary", result)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.source, "notification")
        self.assertEqual(item.citation, "[Notifications]")
        self.assertIn("You have 3 unread notification(s)", item.content)

    def test_failed_tool_result_to_context_item(self):
        from pwanimate.tools.base import ToolResult
        result = ToolResult.fail("Access denied: you must be an approved member of 'Private Lab'.")
        items = tool_result_to_context_items("group_announcements", result)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.source, "system")
        self.assertIn("Access denied", item.content)
        self.assertIsNone(item.citation)

    def test_build_tool_context_package(self):
        from pwanimate.tools.base import ToolResult
        result = ToolResult.ok([{"entity_type": "school", "code": "SPAS", "name": "Pure Sciences", "description": "Desc"}])
        items = tool_result_to_context_items("academic_lookup", result)
        pkg = build_tool_context_package("what is SPAS", items)

        self.assertIsInstance(pkg, ContextPackage)
        self.assertEqual(pkg.total_items, 1)
        self.assertIn("[SPAS]", pkg.citations)
        self.assertIn("<grounding_data source=\"academic\"", pkg.format_context_text())


class OrchestratorToolIntegrationTestCase(TestCase):
    """End-to-end integration tests for PwanimateOrchestrator tool execution."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="student_orch_tool",
            password="password123",
            first_name="Jane",
            last_name="Doe",
        )
        # Seed an academic school
        self.school = School.objects.create(
            code=OfficialSchoolCode.SPAS,
            name="School of Pure and Applied Sciences",
            description="Leading faculty for sciences.",
            is_active=True,
        )
        # Seed an announcement notification for user
        self.notif = NotificationObject.objects.create(
            recipient=self.user,
            notification_type="SYSTEM",
            category="SYSTEM",
            priority="NORMAL",
            title="Class Rescheduled",
            summary="Monday class moved to 2pm.",
            status="CREATED",
        )

        # Build mock gateway
        self.mock_provider = MockLLMProvider(default_response_text="Based on the official records, here is the information.")
        router = LLMRouter(default_provider="mock", providers={"mock": self.mock_provider})
        self.gateway = AIGateway(router=router)

        self.orchestrator = PwanimateOrchestrator(
            gateway=self.gateway,
            tool_registry=get_default_tool_registry(),
        )

    def test_academic_lookup_query_executes_tool(self):
        req = OrchestrationRequest(
            query="what is SPAS",
            user=self.user,
            provider="mock",
        )
        resp = self.orchestrator.run(req)

        self.assertEqual(resp.metadata.get("intent"), "tool")
        self.assertEqual(resp.metadata.get("tool_name"), "academic_lookup")
        self.assertTrue(resp.metadata.get("tool_success"))
        # Verify citations and sources are preserved
        self.assertIn("[SPAS]", resp.citations)
        self.assertTrue(any(s["source"] == "academic" for s in resp.sources))

    def test_notification_query_executes_tool(self):
        req = OrchestrationRequest(
            query="what are my unread notifications?",
            user=self.user,
            provider="mock",
        )
        resp = self.orchestrator.run(req)

        self.assertEqual(resp.metadata.get("intent"), "tool")
        self.assertEqual(resp.metadata.get("tool_name"), "notification_summary")
        self.assertTrue(resp.metadata.get("tool_success"))
        self.assertIn("[Notifications]", resp.citations)
        self.assertTrue(any(s["source"] == "notification" for s in resp.sources))

    def test_non_tool_query_falls_back_to_rag(self):
        req = OrchestrationRequest(
            query="explain quicksort and bubble sort algorithms in python",
            user=self.user,
            provider="mock",
        )
        resp = self.orchestrator.run(req)

        # Standard RAG intent
        self.assertEqual(resp.metadata.get("intent"), "rag")

    def test_conversational_query_skips_tools_and_retrieval(self):
        req = OrchestrationRequest(
            query="hello pwanimate",
            user=self.user,
            provider="mock",
        )
        resp = self.orchestrator.run(req)

        self.assertEqual(resp.metadata.get("intent"), "conversational")
        self.assertEqual(resp.citations, [])
        self.assertEqual(resp.sources, [])

    def test_permission_denied_handled_gracefully(self):
        # Create private group without user membership
        creator = User.objects.create_user(username="prof_x", password="pass")
        priv_grp = Group.objects.create(
            name="Confidential Exam Committee",
            created_by=creator,
            post_visibility=PostVisibility.MEMBERS_ONLY,
        )

        req = OrchestrationRequest(
            query="what was posted in Confidential Exam Committee group?",
            user=self.user,
            provider="mock",
        )
        resp = self.orchestrator.run(req)

        self.assertEqual(resp.metadata.get("intent"), "tool")
        self.assertEqual(resp.metadata.get("tool_name"), "group_announcements")
        # Tool execution failed cleanly due to permission error
        self.assertFalse(resp.metadata.get("tool_success"))
        # Does not crash; grounded explanation generated by LLM
        self.assertIsNotNone(resp.answer)


class ArchitecturalConstraintsTestCase(TestCase):
    """Verify zero loopback HTTP and gateway contract invariants."""

    def setUp(self):
        self.user = User.objects.create_user(username="arch_student", password="password123")
        self.mock_provider = MockLLMProvider(default_response_text="Grounded answer.")
        router = LLMRouter(default_provider="mock", providers={"mock": self.mock_provider})
        self.gateway = AIGateway(router=router)
        self.orchestrator = PwanimateOrchestrator(
            gateway=self.gateway,
            tool_registry=get_default_tool_registry(),
        )

    @patch("urllib.request.urlopen")
    @patch("requests.api.request")
    def test_tool_routing_and_execution_has_zero_loopback_http(self, mock_requests, mock_urllib):
        mock_requests.side_effect = AssertionError("HTTP request attempted via requests!")
        mock_urllib.side_effect = AssertionError("HTTP request attempted via urllib!")

        req = OrchestrationRequest(
            query="my notifications",
            user=self.user,
            provider="mock",
        )
        resp = self.orchestrator.run(req)
        self.assertEqual(resp.metadata.get("intent"), "tool")

        # Confirm mocks were never invoked
        mock_requests.assert_not_called()
        mock_urllib.assert_not_called()
