"""Instructional progress layered on existing sessions/runs, not another runtime."""
import uuid
from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone

PHASES = [(value, value.title()) for value in ['DIAGNOSE','EXPLAIN','DEMONSTRATE','PRACTICE','EVALUATE','ADAPT']]
MODES = [(value, value.title()) for value in ['QUICK','GUIDED','DEEP','PRACTICE']]


class SparkTeachingState(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.OneToOneField('pwanimate.PwanimateStudySession', on_delete=models.CASCADE, related_name='teaching')
    concept = models.ForeignKey('pwanimate.SparkUnitConcept', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    active_run = models.ForeignKey('pwanimate.SparkAgentRun', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    mode = models.CharField(max_length=12, choices=MODES, default='GUIDED')
    phase = models.CharField(max_length=16, choices=PHASES, default='DIAGNOSE')
    objective = models.CharField(max_length=500)
    lesson_key = models.CharField(max_length=40, default='general')
    version = models.PositiveIntegerField(default=1)
    hint_level = models.PositiveSmallIntegerField(default=0)
    difficulty = models.PositiveSmallIntegerField(default=1)
    practice_round = models.PositiveIntegerField(default=0)
    recent_errors = models.PositiveSmallIntegerField(default=0)
    exercise = models.JSONField(default=dict, blank=True)
    question = models.ForeignKey('pwanimate.SparkConceptQuestion', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='teaching_states')
    asked = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(hint_level__gte=0, hint_level__lte=4), name='spark_tutor_hint_bounds'),
            models.CheckConstraint(condition=models.Q(difficulty__gte=1, difficulty__lte=3), name='spark_tutor_difficulty_bounds')]

    def save(self, *args, **kwargs):
        if self.concept_id and (self.concept.record_id != self.session.learning_record_id
                or self.concept.record.user_id != self.session.user_id):
            raise ValidationError('Teaching concept must match the owned unit/session term.')
        if self.question_id and (self.question.source_session_id != self.session_id or self.question.concept_id != self.concept_id):
            raise ValidationError('Teaching question must match its concept and source session.')
        return super().save(*args, **kwargs)


class SparkTeachingTurn(models.Model):
    """Educational outcomes and assistance receipts; contains no private reasoning."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    state = models.ForeignKey(SparkTeachingState, on_delete=models.CASCADE, related_name='turns')
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_message = models.OneToOneField('pwanimate.PwanimateMessage', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='completed_teaching_turn')
    receipt_key = models.CharField(max_length=100)
    run = models.ForeignKey('pwanimate.SparkAgentRun', null=True, blank=True, on_delete=models.SET_NULL, related_name='teaching_turns')
    answer_interaction = models.OneToOneField('pwanimate.SparkAgentInteraction', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='teaching_answer')
    question_interaction = models.ForeignKey('pwanimate.SparkAgentInteraction', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+')
    question = models.ForeignKey('pwanimate.SparkConceptQuestion', null=True, blank=True,
        on_delete=models.RESTRICT, related_name='teaching_turns')
    from_phase = models.CharField(max_length=16, choices=PHASES)
    to_phase = models.CharField(max_length=16, choices=PHASES)
    action = models.CharField(max_length=40)
    activity_kind = models.CharField(max_length=40, default='topic_exposure')
    assistance = models.CharField(max_length=20, default='none')
    evaluation = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict)
    response_markdown = models.TextField(blank=True)
    metrics = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['created_at','id']
        constraints = [models.UniqueConstraint(fields=['state','receipt_key'], name='spark_tutor_turn_once')]
