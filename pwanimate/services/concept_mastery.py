"""Owner-scoped deterministic mastery; no model can submit a verdict/state here."""
import hashlib
import json
import re
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from pwanimate.assessment_validation import REFERENCES, grade, validate_questions, numerical
from pwanimate.mastery_policy import evaluate, policy_config
from pwanimate.models import (SparkAcademicTrackingPreference, SparkAssessment, SparkUnitLearningRecord,
    SparkUnitActivity, SparkUnitConcept, SparkConceptPrerequisite, SparkConceptQuestion,
    SparkConceptEvidence, SparkConceptTransition, PwanimateStudySession)
from pwanimate.services.unit_learning import UnitLearningService


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(',', ':')).encode()).hexdigest()


def question_snapshot(item):
    return {key: item.get(key) for key in ['qid', 'prompt', 'answer_key', 'type', 'options',
        'option_ids', 'reference_id', 'validation_version', 'certification_status']}


def find_question(assessment, qid):
    questions = assessment.questions if isinstance(assessment.questions, list) else []
    matches = [q for q in questions if isinstance(q, dict) and q.get('qid') == qid]
    return matches[0] if len(matches) == 1 else None


class ConceptMasteryService:
    @staticmethod
    def owned_record(user, record_id):
        if not user or not user.is_authenticated:
            raise ValueError('Authentication required')
        record = SparkUnitLearningRecord.objects.filter(pk=record_id, user=user).first()
        if record is None:
            raise LookupError('Learning record not found')
        return record

    @classmethod
    def lock_record(cls, user, record_id):
        """Inside atomic: optional conversation -> consent -> enrollment -> record
        -> session (if needed) -> teaching state/turn or assessment -> evidence.

        Never first acquire these locks holding child session/state/turn locks:
        reentry is safe only when the same parent locks are already held. Evidence INSERTs
        acquire PostgreSQL FK key-share locks on their source session. Taking
        a session FOR UPDATE before consent can deadlock a concurrent grader.
        Lifecycle-only writers may lock conversation/session without consent;
        they must not subsequently request these authorization locks.
        """
        record = cls.owned_record(user, record_id)
        pref = SparkAcademicTrackingPreference.objects.select_for_update().filter(user=user).first()
        if not pref or pref.mode == 'disabled' or not record.authorized_at or record.consent_revision != pref.revision:
            raise ValueError('Current academic tracking authorization is required')
        enrollment = UnitLearningService.enrollments(user).select_for_update(of=('self',)).filter(pk=record.enrollment_id).first()
        if enrollment is None:
            raise ValueError('The enrollment is no longer owned by this student')
        record = SparkUnitLearningRecord.objects.select_for_update().get(pk=record.pk, user=user)
        UnitLearningService.require_writable(record, user)
        return record

    @classmethod
    def owned_concept(cls, user, concept_id):
        concept = SparkUnitConcept.objects.select_related('record').filter(pk=concept_id, record__user=user).first()
        if concept is None:
            raise LookupError('Concept not found')
        return concept

    @classmethod
    @transaction.atomic
    def create_concept(cls, user, record_id, *, key, title, description='', parent_id=None, prerequisites=None):
        record = cls.lock_record(user, record_id)
        if not isinstance(key, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', key) or len(key) > 100:
            raise ValueError('Concept key must be a stable lowercase slug')
        if not isinstance(title, str) or not title.strip() or len(title) > 200 or not isinstance(description, str) or len(description) > 3000:
            raise ValueError('Invalid concept title or description')
        if record.concepts.count() >= 200 and not record.concepts.filter(key=key).exists():
            raise ValueError('WP1 supports at most 200 explicit concepts per unit record')
        concept, created = SparkUnitConcept.objects.get_or_create(record=record, key=key,
            defaults={'title': title.strip(), 'description': description})
        if created:
            SparkConceptTransition.objects.create(concept=concept, version=1, from_state='NOT_STARTED',
                to_state='NOT_STARTED', policy_version='initial', decision={'reason': 'explicit_concept_creation'})
            cls._set_graph(record, concept, parent_id, prerequisites or [])
        return concept

    @classmethod
    def _set_graph(cls, record, concept, parent_id, prerequisite_ids):
        if not isinstance(prerequisite_ids, list) or len(prerequisite_ids) > 20:
            raise ValueError('prerequisites must contain at most 20 concept IDs')
        nodes = {str(c.pk): c for c in record.concepts.all()}
        targets = [str(value) for value in prerequisite_ids]
        parent = str(parent_id) if parent_id else None
        if len(set(targets)) != len(targets) or any(target not in nodes for target in targets) or (parent and parent not in nodes):
            raise ValueError('Relationships must reference unique concepts in this same unit and term')
        graph = {key: [str(pk) for pk in c.prerequisites.values_list('pk', flat=True)]
            + ([str(c.parent_id)] if c.parent_id else []) for key, c in nodes.items()}
        graph[str(concept.pk)] = targets + ([parent] if parent else [])
        seen, visiting = set(), set()
        def visit(key):
            if key in visiting:
                raise ValueError('Concept prerequisites/parent topics must be acyclic')
            if key in seen:
                return
            visiting.add(key)
            for child in graph[key]:
                visit(child)
            visiting.remove(key)
            seen.add(key)
        for key in graph:
            visit(key)
        concept.parent_id = parent_id
        concept.save(update_fields=['parent'])
        concept.prerequisite_edges.all().delete()
        for target in targets:
            SparkConceptPrerequisite.objects.create(concept=concept, prerequisite=nodes[target])

    @classmethod
    @transaction.atomic
    def update_concept(cls, user, concept_id, **changes):
        concept = cls.owned_concept(user, concept_id)
        record = cls.lock_record(user, concept.record_id)
        if set(changes) - {'title', 'description', 'parent_id', 'prerequisites'}:
            raise ValueError('Identity, state and verification cannot be assigned by a client')
        concept.refresh_from_db()
        for key in ('title', 'description'):
            if key in changes:
                value = changes[key]
                if not isinstance(value, str) or len(value) > (200 if key == 'title' else 3000) or (key == 'title' and not value.strip()):
                    raise ValueError('Invalid concept metadata')
                setattr(concept, key, value)
        concept.save()
        if 'parent_id' in changes or 'prerequisites' in changes:
            cls._set_graph(record, concept, changes.get('parent_id', concept.parent_id),
                changes.get('prerequisites', list(concept.prerequisites.values_list('pk', flat=True))))
        cls._recalculate_record(record)
        concept.refresh_from_db()
        return concept

    @classmethod
    def _bind_question(cls, concept, assessment, item, *, assistance='unknown', purpose='practice'):
        """Trusted server integration only; no HTTP/tool can set assistance or difficulty."""
        if assistance not in {'none', 'hint', 'worked_solution', 'unknown'} or purpose not in {'diagnostic', 'practice', 'retention'}:
            raise ValueError('Invalid server delivery metadata')
        ref = REFERENCES.get(item.get('reference_id'), {})
        if ref and ref.get('concept_key') != concept.key:
            raise ValueError('Reference does not assess this concept')
        binding, created = SparkConceptQuestion.objects.get_or_create(assessment=assessment, qid=item['qid'], defaults={
            'concept': concept, 'snapshot': question_snapshot(item), 'source_session': assessment.session,
            'reference_id': item.get('reference_id', ''), 'reference_digest': digest(ref) if ref else '',
            'question_identity': ref.get('identity') or digest(item.get('prompt')),
            'difficulty': ref.get('difficulty', 1), 'assistance': assistance, 'purpose': purpose,
            'delivered_at': timezone.now()})
        if not created and binding.concept_id != concept.pk:
            raise ValueError('A question is already bound to another concept')
        return binding

    @classmethod
    @transaction.atomic
    def bind_existing_question(cls, user, concept_id, assessment_id, qid, *, assistance='unknown', purpose='practice'):
        """Trusted importer; default unknown assistance cannot certify mastery."""
        concept = cls.owned_concept(user, concept_id)
        cls.lock_record(user, concept.record_id)
        assessment = SparkAssessment.objects.select_for_update().filter(pk=assessment_id, user=user).first()
        if assessment is None:
            raise LookupError('Assessment not found')
        item = find_question(assessment, qid)
        if item is None:
            raise ValueError('Question not found')
        if item.get('student_answer') is not None:
            raise ValueError('Bind delivery provenance before the student answers')
        return cls._bind_question(concept, assessment, item, assistance=assistance, purpose=purpose)

    @classmethod
    @transaction.atomic
    def deliver_reference_assessment(cls, user, concept_id, session_id, *, purpose='practice', count=3, target_difficulty=None):
        concept = cls.owned_concept(user, concept_id)
        cls.lock_record(user, concept.record_id)
        session = PwanimateStudySession.objects.filter(pk=session_id, user=user, learning_record=concept.record, status='active').first()
        if session is None:
            raise ValueError('An active owned session for this unit/term is required')
        if purpose not in {'diagnostic', 'practice', 'retention'} or type(count) is not int or not 1 <= count <= 6:
            raise ValueError('Invalid assessment purpose/count')
        # WP1 deliberately offers one tiny explicit bank, not a syllabus generator.
        unit_text = (concept.record.academic_unit.name + ' ' + concept.record.academic_unit.description).casefold()
        bank = [(key, ref) for key, ref in REFERENCES.items() if ref.get('concept_key') == concept.key
            and any(word in unit_text for word in ('algebra', 'matrices', 'matrix', 'determinant', 'mathematics'))]
        if not bank or count > len(bank):
            raise ValueError('No sufficient trusted reference bank is available for this concept/unit')
        if target_difficulty is not None and (type(target_difficulty) is not int or target_difficulty not in {1,2,3}):
            raise ValueError('Invalid target difficulty')
        used = list(concept.question_bindings.values_list('reference_id', flat=True))
        bank.sort(key=lambda pair: (used.count(pair[0]), abs(pair[1].get('difficulty',1)-target_difficulty) if target_difficulty else 0, pair[0]))
        raw = [{'qid': f'q{index + 1}', 'prompt': ref['prompt'], 'answer': ref['expected'], 'reference_id': key}
            for index, (key, ref) in enumerate(bank[:count])]
        assessment = SparkAssessment.objects.create(user=user, session=session, mode='immediate',
            title=f'{purpose.title()} — {concept.title}'[:180], counts_toward_mastery=True,
            questions=validate_questions(raw, count, 'immediate'))
        for item in assessment.questions:
            cls._bind_question(concept, assessment, item, assistance='none', purpose=purpose)
        return assessment

    @staticmethod
    def effective_assistance(spec, at):
        levels = {'none':0, 'unknown':1, 'hint':2, 'worked_solution':3}
        receipts = spec.teaching_turns.filter(created_at__lte=at).values_list('assistance', flat=True)
        return max([spec.assistance, *receipts], key=lambda value: levels.get(value, 1))

    @classmethod
    def _validated_row(cls, evidence):
        row = {'id': str(evidence.pk), 'usable': False, 'verified': False, 'independent': False,
            'outcome': None, 'occurred_at': evidence.occurred_at, 'misconceptions': []}
        if evidence.verification in {'revoked', 'invalid'}:
            return row
        if evidence.activity_id:
            if evidence.activity.record_id == evidence.concept.record_id and evidence.activity.session_id == evidence.session_id:
                row['usable'] = True
            return row
        spec = evidence.question
        assessment = spec.assessment
        item = find_question(assessment, spec.qid)
        if item is None or not isinstance(evidence.grading_result, dict) or not isinstance(spec.snapshot, dict) \
                or digest(question_snapshot(item)) != digest(spec.snapshot) or spec.concept_id != evidence.concept_id \
                or assessment.user_id != evidence.concept.record.user_id or assessment.session_id != evidence.session_id \
                or spec.source_session_id != evidence.session_id \
                or assessment.session.learning_record_id != evidence.concept.record_id \
                or item.get('student_answer') != evidence.student_response or spec.delivered_at > evidence.occurred_at:
            return row
        row['usable'] = True
        ref = REFERENCES.get(spec.reference_id)
        result = grade(spec.snapshot, evidence.student_response)
        if not ref or digest(ref) != spec.reference_digest or result['outcome'] not in {'correct', 'incorrect'} \
                or evidence.verification != 'verified' or result['outcome'] != evidence.grading_result.get('outcome') \
                or item.get('outcome') != result['outcome'] or item.get('correct') is not result['correct']:
            return row
        row.update(verified=True, outcome=result['outcome'], independent=(cls.effective_assistance(spec, evidence.occurred_at) == 'none'
            and evidence.assistance == 'none' and assessment.counts_toward_mastery
            and evidence.evaluation_method == 'reviewed_numeric_v1'),
            identity=spec.question_identity, difficulty=spec.difficulty, context=str(assessment.pk),
            purpose=spec.purpose, delivered_at=spec.delivered_at)
        if result['outcome'] == 'incorrect':
            row['misconceptions'] = [code for answer, code in ref.get('misconceptions', {}).items()
                if numerical(evidence.student_response, answer, '0', '0')['correct'] is True]
        return row

    @classmethod
    def capture_locked(cls, user, assessment, qid):
        spec = SparkConceptQuestion.objects.select_related('concept__record').filter(assessment=assessment, qid=qid).first()
        if spec is None:
            return None
        if assessment.user_id != user.pk:
            raise LookupError('Assessment not found')
        item = find_question(assessment, qid)
        if not item or item.get('student_answer') is None or not item.get('graded_at'):
            raise ValueError('A persisted student response and evaluation are required')
        occurred_at = parse_datetime(item['graded_at'])
        if not occurred_at or timezone.is_naive(occurred_at) or occurred_at > timezone.now() or occurred_at < spec.delivered_at:
            raise ValueError('Invalid grading timestamp')
        if not SparkConceptEvidence.objects.filter(question=spec).exists():
            pref = SparkAcademicTrackingPreference.objects.get(user=user)
            if occurred_at < max(spec.concept.record.authorized_at, pref.changed_at):
                raise ValueError('A response from before current authorization cannot be silently imported')
        if digest(question_snapshot(item)) != digest(spec.snapshot):
            raise ValueError('Question provenance changed; reviewed reissue is required')
        if spec.source_session_id != assessment.session_id:
            raise ValueError('Assessment session provenance changed; reviewed reissue is required')
        result = grade(spec.snapshot, item['student_answer'])
        verified = result['outcome'] in {'correct', 'incorrect'} and digest(REFERENCES.get(spec.reference_id)) == spec.reference_digest \
            and item.get('outcome') == result['outcome'] and item.get('correct') is result['correct']
        assistance = cls.effective_assistance(spec, occurred_at)
        kind = 'attempt'
        if verified and spec.purpose == 'retention':
            kind = 'retention_check'
        elif verified and result['correct'] is True:
            kind = 'assisted_solution' if assistance in {'hint', 'worked_solution'} else \
                'attempt' if assistance == 'unknown' else 'retention_check' if spec.purpose == 'retention' else 'independent_solution'
        evidence, _ = SparkConceptEvidence.objects.get_or_create(question=spec, defaults={
            'concept': spec.concept, 'session': assessment.session, 'kind': kind,
            'student_response': item['student_answer'], 'grading_result': result,
            'assistance': assistance, 'evaluation_method': 'reviewed_numeric_v1' if verified else 'unverified_reference',
            'verification': 'verified' if verified else 'unverified', 'occurred_at': occurred_at})
        cls._recalculate_record(spec.concept.record)
        return evidence

    @classmethod
    @transaction.atomic
    def capture(cls, user, assessment_id, qid):
        assessment = SparkAssessment.objects.select_related('session').filter(pk=assessment_id, user=user).first()
        if assessment is None:
            raise LookupError('Assessment not found')
        if not assessment.session.learning_record_id:
            raise ValueError('Assessment must have an authorized unit record')
        cls.lock_record(user, assessment.session.learning_record_id)
        assessment = SparkAssessment.objects.select_for_update().get(pk=assessment.pk, user=user)
        evidence = cls.capture_locked(user, assessment, qid)
        if evidence is None:
            raise ValueError('A server-authored concept/question binding is required')
        return evidence

    @classmethod
    @transaction.atomic
    def record_activity(cls, user, concept_id, activity_id):
        concept = cls.owned_concept(user, concept_id)
        cls.lock_record(user, concept.record_id)
        activity = SparkUnitActivity.objects.filter(pk=activity_id, record=concept.record).first()
        if activity is None:
            raise LookupError('Activity not found in this unit record')
        evidence, _ = SparkConceptEvidence.objects.get_or_create(concept=concept, activity=activity, defaults={
            'session': activity.session, 'kind': 'self_report' if activity.kind in {'difficulty', 'self_report'} else 'study_activity',
            'student_response': activity.excerpt, 'evaluation_method': 'student_observation',
            'verification': 'unverified', 'occurred_at': activity.occurred_at})
        cls._recalculate_record(concept.record)
        return evidence

    @classmethod
    @transaction.atomic
    def invalidate_evidence(cls, user, evidence_id, reason, *, invalid=False):
        """Trusted evaluator/operator service only; deliberately no student HTTP verdict API."""
        evidence = SparkConceptEvidence.objects.select_related('concept__record').filter(pk=evidence_id, concept__record__user=user).first()
        if evidence is None:
            raise LookupError('Evidence not found')
        # Removing credit must remain possible after tracking revocation or term
        # closure. This trusted correction path cannot create new learning proof.
        SparkAcademicTrackingPreference.objects.select_for_update().filter(user=user).first()
        SparkUnitLearningRecord.objects.select_for_update().get(pk=evidence.concept.record_id, user=user)
        evidence.refresh_from_db()
        if evidence.verification in {'revoked', 'invalid'}:
            return evidence
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 300:
            raise ValueError('An audited revocation reason is required')
        SparkConceptEvidence.objects.filter(pk=evidence.pk).update(verification='invalid' if invalid else 'revoked',
            revoked_at=timezone.now(), revocation_reason=reason)
        cls._recalculate_record(evidence.concept.record)
        evidence.refresh_from_db()
        return evidence

    @classmethod
    def _recalculate_record(cls, record):
        policy, version = policy_config()
        now = timezone.now()
        concepts = {c.pk: c for c in record.concepts.all()}
        ordered, done, visiting = [], set(), set()
        def visit(concept):
            if concept.pk in visiting:
                raise ValueError('Invalid cyclic concept graph')
            if concept.pk in done:
                return
            visiting.add(concept.pk)
            for parent in concept.prerequisites.all():
                if parent.pk not in concepts:
                    raise ValueError('Invalid cross-unit prerequisite')
                visit(concepts[parent.pk])
            visiting.remove(concept.pk)
            done.add(concept.pk)
            ordered.append(concept)
        for concept in concepts.values():
            visit(concept)
        for concept in ordered:
            rows = [cls._validated_row(e) for e in concept.evidence.select_related(
                'concept__record', 'activity', 'question__assessment__session').all()]
            parents = [concepts[pk] for pk in concept.prerequisites.values_list('pk', flat=True)]
            parent_states = ['REVIEW_DUE' if p.state == 'MASTERED' and p.review_due_at and now >= p.review_due_at else p.state for p in parents]
            decision = evaluate(rows, parent_states, now, policy, was_mastered=bool(concept.mastered_at))
            signature = digest({'decision': decision, 'policy': version,
                'evidence_statuses': list(concept.evidence.values_list('id', 'verification')),
                'prerequisites': [(str(p.pk), p.version, p.state) for p in parents]})
            if signature == concept.decision_hash:
                continue
            old = concept.state
            concept.state = decision['state']
            concept.version += 1
            concept.policy_version, concept.decision_hash = version, signature
            for field in ['last_assessed_at', 'last_reviewed_at', 'review_due_at', 'misconceptions']:
                setattr(concept, field, decision[field])
            if concept.state == 'MASTERED' and concept.mastered_at is None:
                concept.mastered_at = now
            SparkUnitConcept.objects.filter(pk=concept.pk).update(**{field: getattr(concept, field) for field in [
                'state', 'version', 'policy_version', 'decision_hash', 'last_assessed_at', 'last_reviewed_at',
                'review_due_at', 'misconceptions', 'mastered_at']})
            SparkConceptTransition.objects.create(concept=concept, version=concept.version, from_state=old,
                to_state=concept.state, policy_version=version, policy=policy,
                decision=json.loads(json.dumps(decision, default=str)), created_at=now)

    @classmethod
    @transaction.atomic
    def recalculate(cls, user, concept_id):
        concept = cls.owned_concept(user, concept_id)
        cls.lock_record(user, concept.record_id)
        cls._recalculate_record(concept.record)
        concept.refresh_from_db()
        return concept

    @staticmethod
    def concept_payload(concept):
        effective = 'REVIEW_DUE' if concept.state == 'MASTERED' and concept.review_due_at and timezone.now() >= concept.review_due_at else concept.state
        return {'id': str(concept.pk), 'record_id': str(concept.record_id), 'key': concept.key,
            'title': concept.title, 'description': concept.description, 'parent_id': str(concept.parent_id) if concept.parent_id else None,
            'prerequisites': [str(pk) for pk in concept.prerequisites.values_list('pk', flat=True)],
            'state': concept.state, 'effective_state': effective, 'version': concept.version,
            'policy_version': concept.policy_version, 'last_assessed_at': concept.last_assessed_at,
            'last_reviewed_at': concept.last_reviewed_at, 'review_due_at': concept.review_due_at,
            'mastered_at': concept.mastered_at, 'misconceptions': concept.misconceptions,
            'evaluation_semantics': 'Versioned policy snapshot; recalculate after provenance changes'}

    @classmethod
    def evidence_payload(cls, evidence):
        row = cls._validated_row(evidence)
        return {'id': str(evidence.pk), 'kind': evidence.kind, 'verification': evidence.verification,
            'currently_usable': row['usable'], 'currently_verified': row['verified'],
            'eligible_for_mastery': row['independent'],
            'evaluation_method': evidence.evaluation_method, 'assistance': evidence.assistance,
            'student_response': evidence.student_response, 'grading_result': evidence.grading_result,
            'session_id': str(evidence.session_id), 'occurred_at': evidence.occurred_at,
            'assessment_id': str(evidence.question.assessment_id) if evidence.question_id else None,
            'qid': evidence.question.qid if evidence.question_id else None,
            'purpose': evidence.question.purpose if evidence.question_id else None,
            'difficulty': evidence.question.difficulty if evidence.question_id else None,
            'activity_id': str(evidence.activity_id) if evidence.activity_id else None,
            'revoked_at': evidence.revoked_at, 'revocation_reason': evidence.revocation_reason}

    @classmethod
    def unit_summary(cls, user, record_id):
        record = cls.owned_record(user, record_id)
        concepts = list(record.concepts.all())
        counts = {key: 0 for key in ['NOT_STARTED', 'LEARNING', 'PRACTICING', 'MASTERED', 'REVIEW_DUE']}
        for concept in concepts:
            counts[cls.concept_payload(concept)['effective_state']] += 1
        return {'record_id': str(record.pk), 'semester_id': record.semester_id, 'concept_count': len(concepts),
            'states': counts, 'coverage': 'explicitly authored concepts only; not complete syllabus coverage',
            'probability': None, 'policy': policy_config()[1]}
