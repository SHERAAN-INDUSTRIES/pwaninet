"""
Tests for Spark's write actions.

The point of these tests is not that the actions work. It is that they
cannot happen by accident. Every rule the design promises is asserted here:

* Spark proposes; the student approves. Nothing is sent by the tool.
* A retry does not act twice.
* The proposal carries the exact message that would be sent.
* The rate limit holds.
* A confirmed action can be undone.
* Blocks, self-targeting and missing users are refused.
"""

from django.test import TestCase

from pwanimate.models import PendingAction
from pwanimate.services.actions import (
    ActionError,
    ActionService,
    MAX_EXECUTIONS_PER_HOUR,
    MAX_PROPOSALS_PER_HOUR,
)
from pwanimate.tools.domain.actions import FollowUserTool, SendMessageTool
from pwanimate.tools.exceptions import ToolPermissionError, ToolValidationError
from users.models import Block, Follow, User


class ActionProposalTests(TestCase):
    """Proposing is safe. That is the whole point."""

    def setUp(self):
        self.student = User.objects.create_user(
            username="action_student", email="act_student@vasto.local", password="pass"
        )
        self.friend = User.objects.create_user(
            username="action_friend", email="act_friend@vasto.local", password="pass"
        )
        self.tool = SendMessageTool()

    def test_the_tool_does_not_send_anything(self):
        """The tool proposes. It must not touch the messaging tables."""
        from messaging.models import Message

        result = self.tool.execute(
            user=self.student, username="action_friend", message="See you at 8"
        )
        self.assertTrue(result.success)
        self.assertEqual(Message.objects.count(), 0)
        self.assertTrue(result.data["needs_approval"])
        self.assertEqual(PendingAction.objects.count(), 1)

    def test_the_proposal_carries_the_exact_message(self):
        """A dry run is only honest if it is the real thing."""
        result = self.tool.execute(
            user=self.student, username="action_friend", message="See you at 8"
        )
        action = PendingAction.objects.get()
        self.assertEqual(action.params["message"], "See you at 8")
        self.assertEqual(action.params["username"], "action_friend")
        self.assertIn("See you at 8", action.summary)
        self.assertEqual(action.status, PendingAction.STATUS_PENDING)
        self.assertTrue(action.is_actionable)

    def test_proposing_the_same_action_twice_returns_one_action(self):
        self.tool.execute(user=self.student, username="action_friend", message="Hi")
        self.tool.execute(user=self.student, username="action_friend", message="Hi")
        self.assertEqual(PendingAction.objects.count(), 1)

    def test_a_different_message_is_a_different_action(self):
        self.tool.execute(user=self.student, username="action_friend", message="Hi")
        self.tool.execute(user=self.student, username="action_friend", message="Hello")
        self.assertEqual(PendingAction.objects.count(), 2)

    def test_an_empty_message_is_refused(self):
        with self.assertRaises(ToolValidationError):
            self.tool.execute(user=self.student, username="action_friend", message="   ")

    def test_an_overlong_message_is_refused(self):
        with self.assertRaises(ToolValidationError):
            self.tool.execute(
                user=self.student, username="action_friend", message="x" * 1001
            )

    def test_an_unknown_user_is_refused(self):
        with self.assertRaises(ToolValidationError):
            self.tool.execute(user=self.student, username="nobody", message="Hi")

    def test_you_cannot_message_yourself(self):
        with self.assertRaises(ToolValidationError):
            self.tool.execute(user=self.student, username="action_student", message="Hi")

    def test_a_blocked_person_cannot_be_messaged(self):
        Block.objects.create(blocker=self.friend, blocked=self.student)
        with self.assertRaises(ToolPermissionError):
            self.tool.execute(user=self.student, username="action_friend", message="Hi")

    def test_an_anonymous_student_cannot_propose(self):
        with self.assertRaises(ToolPermissionError):
            self.tool.execute(user=None, username="action_friend", message="Hi")

    def test_the_proposal_rate_limit_holds(self):
        for index in range(MAX_PROPOSALS_PER_HOUR):
            self.tool.execute(
                user=self.student, username="action_friend", message=f"Hi {index}"
            )
        with self.assertRaises(ToolPermissionError):
            self.tool.execute(user=self.student, username="action_friend", message="One too many")


class ActionConfirmationTests(TestCase):
    """Confirming is the only thing that acts."""

    def setUp(self):
        self.student = User.objects.create_user(
            username="confirm_student", email="confirm_student@vasto.local", password="pass"
        )
        self.friend = User.objects.create_user(
            username="confirm_friend", email="confirm_friend@vasto.local", password="pass"
        )
        self.tool = SendMessageTool()

    def _propose(self, message="See you at 8"):
        self.tool.execute(user=self.student, username="confirm_friend", message=message)
        return PendingAction.objects.get()

    def test_confirming_sends_the_message(self):
        from messaging.models import Message

        action = self._propose()
        self.assertEqual(Message.objects.count(), 0)

        confirmed = ActionService.confirm(self.student, action.id)
        self.assertEqual(confirmed.status, PendingAction.STATUS_CONFIRMED)
        self.assertEqual(Message.objects.count(), 1)

        message = Message.objects.get()
        self.assertEqual(message.content, "See you at 8")
        self.assertEqual(message.sender_id, self.student.id)
        self.assertTrue(confirmed.result["message_id"])

    def test_confirming_twice_sends_once(self):
        from messaging.models import Message

        action = self._propose()
        ActionService.confirm(self.student, action.id)
        ActionService.confirm(self.student, action.id)
        self.assertEqual(Message.objects.count(), 1)

    def test_you_cannot_confirm_someone_elses_action(self):
        action = self._propose()
        other = User.objects.create_user(
            username="confirm_other", email="confirm_other@vasto.local", password="pass"
        )
        with self.assertRaises(ActionError):
            ActionService.confirm(other, action.id)

    def test_cancelling_sends_nothing(self):
        from messaging.models import Message

        action = self._propose()
        cancelled = ActionService.cancel(self.student, action.id)
        self.assertEqual(cancelled.status, PendingAction.STATUS_CANCELLED)
        self.assertEqual(Message.objects.count(), 0)

    def test_a_cancelled_action_cannot_be_confirmed(self):
        action = self._propose()
        ActionService.cancel(self.student, action.id)
        with self.assertRaises(ActionError):
            ActionService.confirm(self.student, action.id)

    def test_an_expired_proposal_cannot_be_confirmed(self):
        from django.utils import timezone

        action = self._propose()
        action.expires_at = timezone.now() - timezone.timedelta(minutes=1)
        action.save(update_fields=["expires_at"])
        with self.assertRaises(ActionError):
            ActionService.confirm(self.student, action.id)
        action.refresh_from_db()
        self.assertEqual(action.status, PendingAction.STATUS_EXPIRED)

    def test_the_execution_rate_limit_holds(self):
        for index in range(MAX_EXECUTIONS_PER_HOUR):
            action = self._propose(f"Message {index}")
            ActionService.confirm(self.student, action.id)
        action = self._propose("One too many")
        with self.assertRaises(ActionError):
            ActionService.confirm(self.student, action.id)


class ActionUndoTests(TestCase):
    """Undo is what makes a write action safe enough to ship."""

    def setUp(self):
        self.student = User.objects.create_user(
            username="undo_student", email="undo_student@vasto.local", password="pass"
        )
        self.friend = User.objects.create_user(
            username="undo_friend", email="undo_friend@vasto.local", password="pass"
        )

    def test_a_sent_message_can_be_undone(self):
        from messaging.models import Message

        tool = SendMessageTool()
        tool.execute(user=self.student, username="undo_friend", message="Oops")
        action = PendingAction.objects.get()
        ActionService.confirm(self.student, action.id)

        undone = ActionService.undo(self.student, action.id)
        self.assertEqual(undone.status, PendingAction.STATUS_UNDONE)
        self.assertTrue(Message.objects.get().is_deleted)

    def test_a_follow_can_be_undone(self):
        tool = FollowUserTool()
        tool.execute(user=self.student, username="undo_friend")
        action = PendingAction.objects.get()
        ActionService.confirm(self.student, action.id)
        self.assertTrue(
            Follow.objects.filter(follower=self.student, followed=self.friend).exists()
        )

        undone = ActionService.undo(self.student, action.id)
        self.assertEqual(undone.status, PendingAction.STATUS_UNDONE)
        self.assertFalse(
            Follow.objects.filter(follower=self.student, followed=self.friend).exists()
        )

    def test_you_cannot_undo_someone_elses_action(self):
        tool = FollowUserTool()
        tool.execute(user=self.student, username="undo_friend")
        action = PendingAction.objects.get()
        other = User.objects.create_user(
            username="undo_other", email="undo_other@vasto.local", password="pass"
        )
        with self.assertRaises(ActionError):
            ActionService.undo(other, action.id)


class FollowActionTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username="follow_student", email="follow_student@vasto.local", password="pass"
        )
        self.teacher = User.objects.create_user(
            username="follow_teacher", email="follow_teacher@vasto.local", password="pass"
        )
        self.tool = FollowUserTool()

    def test_the_follow_tool_does_not_follow(self):
        result = self.tool.execute(user=self.student, username="follow_teacher")
        self.assertTrue(result.success)
        self.assertEqual(Follow.objects.count(), 0)
        self.assertEqual(PendingAction.objects.count(), 1)

    def test_confirming_follows(self):
        self.tool.execute(user=self.student, username="follow_teacher")
        action = PendingAction.objects.get()
        ActionService.confirm(self.student, action.id)
        self.assertTrue(
            Follow.objects.filter(follower=self.student, followed=self.teacher).exists()
        )

    def test_you_cannot_follow_yourself(self):
        with self.assertRaises(ToolValidationError):
            self.tool.execute(user=self.student, username="follow_student")
