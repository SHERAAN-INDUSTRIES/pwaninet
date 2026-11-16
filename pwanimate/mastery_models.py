"""SALS 0.3 WP1 metadata, immutable provenance and versioned decisions.

Enrollment, terms, sessions and assessments remain in their existing models.
"""
import uuid
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

STATES = [(state, state.replace('_', ' ').title()) for state in
    ['NOT_STARTED', 'LEARNING', 'PRACTICING', 'MASTERED', 'REVIEW_DUE']]
ASSISTANCE = [(value, value.replace('_', ' ').title()) for value in ['none', 'hint', 'worked_solution', 'unknown']]


class SparkUnitConcept(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    record = models.ForeignKey('pwanimate.SparkUnitLearningRecord', on_delete=models.CASCADE, related_name='concepts')
    key = models.SlugField(max_length=100)
    title = models.CharField(max_length=200)
    description = models.TextField(max_length=3000, blank=True)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='children')
    prerequisites = models.ManyToManyField('self', through='SparkConceptPrerequisite', symmetrical=False, related_name='dependents')
    state = models.CharField(max_length=16, choices=STATES, default='NOT_STARTED')
    version = models.PositiveIntegerField(default=1)
    policy_version = models.CharField(max_length=64, blank=True)
    decision_hash = models.CharField(max_length=64, blank=True)
    last_assessed_at = models.DateTimeField(null=True, blank=True)
    last_reviewed_at = models.DateTimeField(null=True, blank=True)
    mastered_at = models.DateTimeField(null=True, blank=True)
    review_due_at = models.DateTimeField(null=True, blank=True)
    misconceptions = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['key']
        constraints = [models.UniqueConstraint(fields=['record', 'key'], name='spark_concept_record_key'),
            models.CheckConstraint(condition=models.Q(state__in=[s[0] for s in STATES]), name='spark_concept_valid_state')]

    def save(self, *args, **kwargs):
        if self.parent_id and self.parent.record_id != self.record_id:
            raise ValidationError('Parent must belong to the same student unit and term.')
        if self._state.adding:
            if self.state != 'NOT_STARTED' or self.version != 1:
                raise ValidationError('Initial mastery must be NOT_STARTED version 1.')
        else:
            old = type(self).objects.get(pk=self.pk)
            for field in ['record_id', 'key', 'state', 'version', 'policy_version', 'decision_hash',
                          'last_assessed_at', 'last_reviewed_at', 'mastered_at', 'review_due_at', 'misconceptions']:
                if getattr(old, field) != getattr(self, field):
                    raise ValidationError('Concept identity and mastery are managed by the evidence policy.')
        return super().save(*args, **kwargs)


class SparkConceptPrerequisite(models.Model):
    concept = models.ForeignKey(SparkUnitConcept, on_delete=models.CASCADE, related_name='prerequisite_edges')
    prerequisite = models.ForeignKey(SparkUnitConcept, on_delete=models.CASCADE, related_name='dependent_edges')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['concept', 'prerequisite'], name='spark_concept_prereq_once'),
            models.CheckConstraint(condition=~models.Q(concept=models.F('prerequisite')), name='spark_concept_not_own_prereq')]

    def save(self, *args, **kwargs):
        if self.concept.record_id != self.prerequisite.record_id:
            raise ValidationError('Prerequisites must belong to the same student unit and term.')
        return super().save(*args, **kwargs)


class FrozenPayload(models.Model):
    """Ordinary model saves cannot edit historical provenance (raw SQL is trusted)."""
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Historical provenance is immutable; use audited invalidation.')
        return super().save(*args, **kwargs)


class SparkConceptQuestion(FrozenPayload):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    concept = models.ForeignKey(SparkUnitConcept, on_delete=models.CASCADE, related_name='question_bindings')
    assessment = models.ForeignKey('pwanimate.SparkAssessment', on_delete=models.RESTRICT, related_name='concept_questions')
    source_session = models.ForeignKey('pwanimate.PwanimateStudySession', on_delete=models.RESTRICT, related_name='concept_questions')
    qid = models.CharField(max_length=60)
    reference_id = models.CharField(max_length=100, blank=True)
    reference_digest = models.CharField(max_length=64, blank=True)
    question_identity = models.CharField(max_length=100)
    difficulty = models.PositiveSmallIntegerField(default=1)
    assistance = models.CharField(max_length=20, choices=ASSISTANCE, default='unknown')
    purpose = models.CharField(max_length=16, choices=[('diagnostic', 'Diagnostic'), ('practice', 'Practice'), ('retention', 'Retention')])
    snapshot = models.JSONField()
    delivered_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['assessment', 'qid'], name='spark_concept_question_once'),
            models.CheckConstraint(condition=models.Q(difficulty__gte=1, difficulty__lte=3), name='spark_concept_difficulty')]

    def save(self, *args, **kwargs):
        session = self.assessment.session
        if self.source_session_id != self.assessment.session_id or session.learning_record_id != self.concept.record_id or session.user_id != self.concept.record.user_id \
                or self.assessment.user_id != session.user_id:
            raise ValidationError('Assessment provenance must match the concept owner, unit and term.')
        return super().save(*args, **kwargs)


class SparkConceptEvidence(FrozenPayload):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    concept = models.ForeignKey(SparkUnitConcept, on_delete=models.CASCADE, related_name='evidence')
    question = models.OneToOneField(SparkConceptQuestion, null=True, blank=True, on_delete=models.RESTRICT, related_name='evidence')
    activity = models.ForeignKey('pwanimate.SparkUnitActivity', null=True, blank=True, on_delete=models.RESTRICT, related_name='concept_evidence')
    session = models.ForeignKey('pwanimate.PwanimateStudySession', on_delete=models.RESTRICT, related_name='concept_evidence')
    kind = models.CharField(max_length=24, choices=[(s, s.replace('_', ' ').title()) for s in
        ['attempt', 'assisted_solution', 'independent_solution', 'retention_check', 'self_report', 'study_activity']])
    student_response = models.TextField(max_length=1000, blank=True)
    grading_result = models.JSONField(default=dict)
    assistance = models.CharField(max_length=20, choices=ASSISTANCE, default='unknown')
    evaluation_method = models.CharField(max_length=40)
    verification = models.CharField(max_length=16, default='unverified', choices=[(s, s.title()) for s in
        ['verified', 'unverified', 'invalid', 'revoked']])
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ['occurred_at', 'id']
        indexes = [models.Index(fields=['concept', 'occurred_at'], name='spark_concept_evidence_time')]
        constraints = [models.CheckConstraint(condition=(models.Q(question__isnull=False, activity__isnull=True)
            | models.Q(question__isnull=True, activity__isnull=False)), name='spark_concept_evidence_source'),
            models.UniqueConstraint(fields=['concept', 'activity'], condition=models.Q(activity__isnull=False), name='spark_concept_activity_once')]

    def save(self, *args, **kwargs):
        if self.session.learning_record_id != self.concept.record_id or self.session.user_id != self.concept.record.user_id:
            raise ValidationError('Evidence session must match its concept owner, unit and term.')
        if self.question_id and self.question.concept_id != self.concept_id:
            raise ValidationError('Evidence question must match its concept.')
        if self.activity_id and (self.activity.record_id != self.concept.record_id or self.activity.session_id != self.session_id):
            raise ValidationError('Evidence activity must match its concept and session.')
        return super().save(*args, **kwargs)


class SparkConceptTransition(FrozenPayload):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    concept = models.ForeignKey(SparkUnitConcept, on_delete=models.CASCADE, related_name='transitions')
    version = models.PositiveIntegerField()
    from_state = models.CharField(max_length=16, choices=STATES)
    to_state = models.CharField(max_length=16, choices=STATES)
    policy_version = models.CharField(max_length=64)
    policy = models.JSONField(default=dict)
    decision = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['version']
        constraints = [models.UniqueConstraint(fields=['concept', 'version'], name='spark_concept_transition_version')]
