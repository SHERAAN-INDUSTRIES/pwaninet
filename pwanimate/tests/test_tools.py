"""
Comprehensive Test Suite for Pwanimate Phase 8A–8C: Read-Only Domain Tools.

Tests:
1. Tool Contracts & Tool Registry (lifecycle, schema generation, dispatch, error handling).
2. AcademicLookupTool (Schools, Departments, Programmes, Courses, inactive filtering, limit capping).
3. DocumentDetailTool (share_id, slug, public vs private authorization, engagement counts, zero storage paths).
4. GroupAnnouncementsTool (public vs members-only authorization, member checks, post payloads).
5. UserProfileTool (privacy levels: public, authenticated, followers, private, masking sensitive fields).
6. NotificationSummaryTool (strict user-scoped queries, unread counts, unread filtering, anonymous rejection).
7. Architectural Constraints (zero loopback HTTP calls, zero external API calls).
"""

import uuid
from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model

from courses.models import School, Department, Programme, Course, OfficialSchoolCode, AcademicLevelType
from documents.models import Document, Category
from documents.engagement.models import DocumentView, DocumentDownload
from groups.models import Group, Membership, MembershipStatus, PostVisibility
from posts.models import Post
from users.models import PrivacyLevel, Follow
from notifications.models import NotificationObject

from pwanimate.tools.base import BaseDomainTool, ToolResult
from pwanimate.tools.exceptions import (
    ToolError,
    ToolNotFoundError,
    ToolValidationError,
    ToolPermissionError,
)
from pwanimate.tools.registry import ToolRegistry, get_default_tool_registry
from pwanimate.tools.domain import (
    AcademicLookupTool,
    DocumentDetailTool,
    GroupAnnouncementsTool,
    UserProfileTool,
    NotificationSummaryTool,
)

User = get_user_model()


class ToolRegistryTestCase(TestCase):
    """Test ToolRegistry contracts, schemas, registration, and execution dispatch."""

    def setUp(self):
        self.registry = ToolRegistry()
        self.academic_tool = AcademicLookupTool()
        self.user = User.objects.create_user(username="test_registry_user", password="password123")

    def test_register_and_get_tool(self):
        self.registry.register(self.academic_tool)
        retrieved = self.registry.get("academic_lookup")
        self.assertEqual(retrieved, self.academic_tool)
        self.assertEqual(retrieved.name, "academic_lookup")

    def test_get_unregistered_tool_raises_tool_not_found(self):
        with self.assertRaises(ToolNotFoundError) as ctx:
            self.registry.get("non_existent_tool")
        self.assertIn("non_existent_tool", str(ctx.exception))

    def test_list_tools(self):
        self.registry.register(self.academic_tool)
        tools = self.registry.list_tools()
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].name, "academic_lookup")

    def test_get_schemas(self):
        self.registry.register(self.academic_tool)
        schemas = self.registry.get_schemas()
        self.assertEqual(len(schemas), 1)
        self.assertEqual(schemas[0]["name"], "academic_lookup")
        self.assertIn("parameters", schemas[0])
        self.assertEqual(schemas[0]["parameters"]["type"], "object")

    def test_default_tool_registry_contains_five_tools(self):
        reg = get_default_tool_registry()
        tool_names = {t.name for t in reg.list_tools()}
        expected_names = {
            "academic_lookup",
            "document_detail",
            "group_announcements",
            "user_profile",
            "notification_summary",
        }
        self.assertTrue(expected_names.issubset(tool_names))

    def test_registry_execute_dispatch_success(self):
        self.registry.register(self.academic_tool)
        result = self.registry.execute("academic_lookup", user=self.user, entity_type="school")
        self.assertTrue(result.success)
        self.assertIsInstance(result.data, list)

    def test_registry_execute_validation_error(self):
        tool = DocumentDetailTool()
        self.registry.register(tool)
        # Neither share_id nor slug provided
        result = self.registry.execute("document_detail", user=self.user)
        self.assertFalse(result.success)
        self.assertIn("validation_error", result.metadata.get("error_type", ""))


class AcademicLookupToolTestCase(TestCase):
    """Test AcademicLookupTool hierarchy search and inactive exclusion."""

    def setUp(self):
        self.user = User.objects.create_user(username="student_academic", password="password123")
        # School
        self.school = School.objects.create(
            code=OfficialSchoolCode.SPAS,
            name="School of Pure and Applied Sciences",
            is_active=True,
        )
        self.inactive_school = School.objects.create(
            code=OfficialSchoolCode.SEDU,
            name="School of Education (Inactive)",
            is_active=False,
        )
        # Department
        self.dept = Department.objects.create(
            code="COMP",
            name="Department of Computing",
            school=self.school,
            is_active=True,
        )
        # Programme
        self.prog = Programme.objects.create(
            code="BSC-CS",
            name="Bachelor of Science in Computer Science",
            school=self.school,
            department=self.dept,
            academic_level=AcademicLevelType.BACHELOR,
            duration_years=4,
            is_active=True,
        )
        # Course
        self.course = Course.objects.create(
            code="CS101",
            name="Introduction to Programming",
            school=self.school,
            department=self.dept,
            programme=self.prog,
            degree_type="Bachelor",
            is_active=True,
        )
        self.tool = AcademicLookupTool()

    def test_lookup_all_active_entities(self):
        result = self.tool.execute(user=self.user, entity_type="all")
        self.assertTrue(result.success)
        names = [item["name"] for item in result.data]
        self.assertIn("School of Pure and Applied Sciences", names)
        self.assertNotIn("School of Education (Inactive)", names)

    def test_lookup_schools_by_code(self):
        result = self.tool.execute(user=self.user, entity_type="school", code="SPAS")
        self.assertTrue(result.success)
        self.assertEqual(len(result.data), 1)
        self.assertEqual(result.data[0]["code"], "SPAS")

    def test_lookup_departments_with_parent_mapping(self):
        result = self.tool.execute(user=self.user, entity_type="department", query="Computing")
        self.assertTrue(result.success)
        self.assertEqual(len(result.data), 1)
        dept_data = result.data[0]
        self.assertEqual(dept_data["code"], "COMP")
        self.assertEqual(dept_data["school_code"], "SPAS")

    def test_lookup_programmes_with_details(self):
        result = self.tool.execute(user=self.user, entity_type="programme", code="BSC-CS")
        self.assertTrue(result.success)
        self.assertEqual(len(result.data), 1)
        prog_data = result.data[0]
        self.assertEqual(prog_data["academic_level"], AcademicLevelType.BACHELOR)
        self.assertEqual(prog_data["duration_years"], 4)

    def test_lookup_limit_capping(self):
        result = self.tool.execute(user=self.user, entity_type="all", limit=50)
        self.assertTrue(result.success)
        # Max allowed limit is 20
        self.assertLessEqual(len(result.data), 20)


class DocumentDetailToolTestCase(TestCase):
    """Test DocumentDetailTool authorization, engagement metrics, and sanitization."""

    def setUp(self):
        self.uploader = User.objects.create_user(username="uploader_doc", password="password123")
        self.other_user = User.objects.create_user(username="student_doc", password="password123")
        self.staff_user = User.objects.create_user(username="admin_doc", password="password123", is_staff=True)

        self.category, _ = Category.objects.get_or_create(
            code="lecture_notes",
            defaults={"name": "Lecture Notes"},
        )
        self.public_doc = Document.objects.create(
            title="Introduction to Linear Algebra",
            slug="intro-linear-algebra",
            category=self.category,
            visibility="public",
            status="ready",
            is_available=True,
            uploaded_by=self.uploader,
            description="Complete lecture notes for MAT 101",
        )
        self.private_doc = Document.objects.create(
            title="Draft Exam Blueprint",
            slug="draft-exam-blueprint",
            category=self.category,
            visibility="private",
            status="ready",
            is_available=True,
            uploaded_by=self.uploader,
            description="Strictly confidential",
        )
        self.draft_doc = Document.objects.create(
            title="Unprocessed Notes",
            slug="unprocessed-notes",
            category=self.category,
            visibility="public",
            status="draft",
            is_available=True,
            uploaded_by=self.uploader,
        )

        # Engagement
        DocumentView.objects.create(document=self.public_doc, user=self.other_user)
        DocumentView.objects.create(document=self.public_doc, user=self.uploader)

        self.tool = DocumentDetailTool()

    def test_public_document_lookup_by_share_id(self):
        result = self.tool.execute(user=self.other_user, share_id=str(self.public_doc.share_id))
        self.assertTrue(result.success)
        data = result.data
        self.assertEqual(data["title"], "Introduction to Linear Algebra")
        self.assertEqual(data["slug"], "intro-linear-algebra")
        self.assertEqual(data["category"], "Lecture Notes")
        self.assertEqual(data["view_count"], 2)
        self.assertEqual(data["canonical_url"], f"/documents/document/{self.public_doc.share_id}/")
        self.assertIn("thumbnail_url", data)
        self.assertIn("media_url", data)
        self.assertEqual(data["resource_type"], "document")
        self.assertEqual(data["author"], "uploader_doc")

    def test_public_document_lookup_by_slug(self):
        result = self.tool.execute(user=None, slug="intro-linear-algebra")
        self.assertTrue(result.success)
        self.assertEqual(result.data["share_id"], str(self.public_doc.share_id))

    def test_private_document_owner_allowed(self):
        result = self.tool.execute(user=self.uploader, share_id=str(self.private_doc.share_id))
        self.assertTrue(result.success)
        self.assertEqual(result.data["title"], "Draft Exam Blueprint")

    def test_private_document_staff_allowed(self):
        result = self.tool.execute(user=self.staff_user, share_id=str(self.private_doc.share_id))
        self.assertTrue(result.success)
        self.assertEqual(result.data["title"], "Draft Exam Blueprint")

    def test_private_document_non_owner_denied(self):
        with self.assertRaises(ToolPermissionError) as ctx:
            self.tool.execute(user=self.other_user, share_id=str(self.private_doc.share_id))
        self.assertIn("permission", str(ctx.exception).lower())

    def test_draft_document_not_found(self):
        with self.assertRaises(ToolNotFoundError):
            self.tool.execute(user=self.uploader, slug="unprocessed-notes")

    def test_storage_paths_and_s3_keys_not_leaked(self):
        result = self.tool.execute(user=self.uploader, share_id=str(self.public_doc.share_id))
        keys = list(result.data.keys())
        for forbidden in ["storage", "file_path", "s3_key", "checksum", "task_id"]:
            self.assertNotIn(forbidden, keys)


class GroupAnnouncementsToolTestCase(TestCase):
    """Test GroupAnnouncementsTool membership authorization and post fetching."""

    def setUp(self):
        self.creator = User.objects.create_user(username="creator_grp", password="password123")
        self.member = User.objects.create_user(username="member_grp", password="password123")
        self.outsider = User.objects.create_user(username="outsider_grp", password="password123")

        # Members-only group
        self.private_group = Group.objects.create(
            name="Compsci Research Lab",
            created_by=self.creator,
            post_visibility=PostVisibility.MEMBERS_ONLY,
        )
        Membership.objects.create(
            user=self.member,
            group=self.private_group,
            status=MembershipStatus.APPROVED,
        )

        # Public group
        self.public_group = Group.objects.create(
            name="Campus Noticeboard",
            created_by=self.creator,
            post_visibility=PostVisibility.EVERYONE,
            is_official=True,
        )

        # Posts
        self.post_priv = Post.objects.create(
            group=self.private_group,
            author=self.creator,
            content="Lab meeting tomorrow at 10 AM.",
        )
        self.post_pub = Post.objects.create(
            group=self.public_group,
            author=self.creator,
            content="Library hours extended for finals.",
        )

        self.tool = GroupAnnouncementsTool()

    def test_public_group_accessible_to_outsider(self):
        result = self.tool.execute(user=self.outsider, group_name="Campus Noticeboard")
        self.assertTrue(result.success)
        posts = result.data["announcements"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["content"], "Library hours extended for finals.")
        self.assertEqual(posts[0]["canonical_url"], f"/post/{self.post_pub.share_id}/")

    def test_members_only_group_accessible_to_approved_member(self):
        result = self.tool.execute(user=self.member, group_id=self.private_group.id)
        self.assertTrue(result.success)
        posts = result.data["announcements"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["content"], "Lab meeting tomorrow at 10 AM.")

    def test_members_only_group_denied_to_outsider(self):
        with self.assertRaises(ToolPermissionError) as ctx:
            self.tool.execute(user=self.outsider, group_id=self.private_group.id)
        self.assertIn("approved member", str(ctx.exception))

    def test_members_only_group_denied_to_anonymous(self):
        with self.assertRaises(ToolPermissionError):
            self.tool.execute(user=None, group_id=self.private_group.id)

    def test_non_existent_group_raises_tool_not_found(self):
        with self.assertRaises(ToolNotFoundError):
            self.tool.execute(user=self.member, group_id=999999)


class UserProfileToolTestCase(TestCase):
    """Test UserProfileTool privacy levels and credential masking."""

    def setUp(self):
        self.viewer = User.objects.create_user(username="viewer_usr", password="password123")
        self.follower = User.objects.create_user(username="follower_usr", password="password123")
        self.staff_user = User.objects.create_user(username="staff_usr", password="password123", is_staff=True)

        self.public_user = User.objects.create_user(
            username="alice_public",
            first_name="Alice",
            last_name="Smith",
            bio="CS Sophomore",
            headline="Open source enthusiast",
            profile_privacy=PrivacyLevel.AUTHENTICATED,
            email="alice@vasto.local",
        )
        # Legacy PUBLIC rows can remain in storage, but are no longer a valid creation choice.
        User.objects.filter(pk=self.public_user.pk).update(profile_privacy=PrivacyLevel.PUBLIC)
        self.public_user.refresh_from_db()
        self.followers_user = User.objects.create_user(
            username="bob_followers",
            first_name="Bob",
            last_name="Jones",
            profile_privacy=PrivacyLevel.FOLLOWERS,
            email="bob@vasto.local",
        )
        Follow.objects.create(follower=self.follower, followed=self.followers_user)

        self.private_user = User.objects.create_user(
            username="charlie_private",
            first_name="Charlie",
            profile_privacy=PrivacyLevel.PRIVATE,
            email="charlie@vasto.local",
        )

        self.tool = UserProfileTool()

    def test_public_profile_accessible_by_anyone(self):
        result = self.tool.execute(user=None, username="alice_public")
        self.assertTrue(result.success)
        data = result.data
        self.assertEqual(data["username"], "alice_public")
        self.assertEqual(data["display_name"], "Alice Smith")
        self.assertEqual(data["bio"], "CS Sophomore")
        # Email hidden for public visitor
        self.assertNotIn("email", data)
        # Password never present
        self.assertNotIn("password", data)

    def test_followers_only_accessible_by_follower(self):
        result = self.tool.execute(user=self.follower, username="bob_followers")
        self.assertTrue(result.success)
        self.assertEqual(result.data["username"], "bob_followers")

    def test_followers_only_denied_to_non_follower(self):
        with self.assertRaises(ToolPermissionError) as ctx:
            self.tool.execute(user=self.viewer, username="bob_followers")
        self.assertIn("followers only", str(ctx.exception).lower())

    def test_followers_only_accessible_by_self(self):
        result = self.tool.execute(user=self.followers_user, username="bob_followers")
        self.assertTrue(result.success)
        # Self gets to see their own email
        self.assertEqual(result.data["email"], "bob@vasto.local")

    def test_private_profile_denied_to_other_users(self):
        with self.assertRaises(ToolPermissionError):
            self.tool.execute(user=self.viewer, username="charlie_private")

    def test_private_profile_accessible_by_self_and_staff(self):
        result_self = self.tool.execute(user=self.private_user, username="charlie_private")
        self.assertTrue(result_self.success)

        result_staff = self.tool.execute(user=self.staff_user, username="charlie_private")
        self.assertTrue(result_staff.success)
        self.assertEqual(result_staff.data["email"], "charlie@vasto.local")


class NotificationSummaryToolTestCase(TestCase):
    """Test NotificationSummaryTool caller-scoping, unread filtering, and counts."""

    def setUp(self):
        self.user_a = User.objects.create_user(username="user_notif_a", password="password123")
        self.user_b = User.objects.create_user(username="user_notif_b", password="password123")

        # Notifications for User A
        self.notif_a1 = NotificationObject.objects.create(
            recipient=self.user_a,
            notification_type="SYSTEM",
            category="SYSTEM",
            priority="HIGH",
            title="Term Begins",
            summary="New semester starts next week.",
            status="CREATED",  # unread
            metadata={"action_url": "/academic/calendar/"},
        )
        self.notif_a2 = NotificationObject.objects.create(
            recipient=self.user_a,
            notification_type="DOCUMENT",
            category="DOCUMENT",
            priority="NORMAL",
            title="Notes Uploaded",
            summary="New notes in MAT 101.",
            status="READ",  # read
            metadata={"action_url": "/documents/mat101/"},
        )

        # Notification for User B
        self.notif_b = NotificationObject.objects.create(
            recipient=self.user_b,
            notification_type="GROUP",
            category="SOCIAL",
            priority="NORMAL",
            title="Group Invite",
            summary="You were invited to Chess Club.",
            status="CREATED",
        )

        self.tool = NotificationSummaryTool()

    def test_anonymous_user_denied(self):
        with self.assertRaises(ToolPermissionError):
            self.tool.execute(user=None)

    def test_strict_caller_scoping_no_leakage_between_users(self):
        result = self.tool.execute(user=self.user_a, unread_only=False)
        self.assertTrue(result.success)
        notif_titles = [n["title"] for n in result.data["notifications"]]
        self.assertIn("Term Begins", notif_titles)
        self.assertIn("Notes Uploaded", notif_titles)
        # User B's notification must NEVER appear
        self.assertNotIn("Group Invite", notif_titles)

    def test_unread_filtering_and_counts(self):
        result_unread = self.tool.execute(user=self.user_a, unread_only=True)
        self.assertTrue(result_unread.success)
        self.assertEqual(result_unread.data["unread_count"], 1)
        self.assertEqual(len(result_unread.data["notifications"]), 1)
        self.assertEqual(result_unread.data["notifications"][0]["title"], "Term Begins")


class ArchitecturalConstraintsTestCase(TestCase):
    """
    Test architectural guarantees:
    - Zero loopback HTTP requests (no internal requests.get/post).
    - Zero external API requests.
    - Pure local ORM execution.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="arch_test_user", password="password123")
        self.registry = get_default_tool_registry()

    @patch("urllib.request.urlopen")
    @patch("requests.api.request")
    def test_tools_execute_with_zero_http_traffic(self, mock_requests, mock_urllib):
        # Configure mocks to raise if invoked
        mock_requests.side_effect = AssertionError("Internal/external HTTP request attempted via requests!")
        mock_urllib.side_effect = AssertionError("Internal/external HTTP request attempted via urllib!")

        # Execute academic lookup
        res_academic = self.registry.execute("academic_lookup", user=self.user, entity_type="school")
        self.assertTrue(res_academic.success)

        # Execute user profile lookup
        res_user = self.registry.execute("user_profile", user=self.user, username="arch_test_user")
        self.assertTrue(res_user.success)

        # Execute notification summary
        res_notif = self.registry.execute("notification_summary", user=self.user)
        self.assertTrue(res_notif.success)

        # Confirm mocks were never called
        mock_requests.assert_not_called()
        mock_urllib.assert_not_called()
