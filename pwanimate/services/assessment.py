"""Quiz and assessment engine for unit-scoped study sessions.

Spark generates questions with answer keys that never leave the server.
Every graded item is produced by server-side evaluation — a student saying
"I understand" moves nothing. Only correctly assessed answers raise
`session.mastery_percent`, which in turn gates `progress_percent`.
"""
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from django.utils import timezone

from pwanimate.ai.gateway.gateway import AIGateway
from pwanimate.ai.gateway.types import ChatMessage, LLMRequest
from pwanimate.models import SparkAssessment
from pwanimate.services.study_session import StudySessionService

logger = logging.getLogger(__name__)

MAX_QUESTIONS = 10
MIN_QUESTIONS = 1
GRADING_PROMPT = (
    "You are grading a student's answer inside a study session. Compare the "
    "student answer to the reference answer for the question. Reply ONLY with "
    "a JSON object {\"correct\": true|false, \"feedback\": \"one short "
    "sentence for the student\"}. Accept equivalent phrasing and correct "
    "reasoning; mark incorrect only when the substance is wrong or missing."
)


def _parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    raw = (text or '').strip()
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except Exception:
        pass
    start, end = raw.find('{'), raw.rfind('}')
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(raw[start:end + 1])
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def public_question(item: Dict[str, Any]) -> Dict[str, Any]:
    """Client-safe question: answer key and raw student answer stay server-side."""
    answered = item.get('correct') is not None
    return {
        'qid': item.get('qid'),
        'prompt': item.get('prompt', ''),
        'options': item.get('options') or None,
        'answered': answered,
        'correct': item.get('correct') if answered else None,
        'feedback': item.get('feedback') if answered else None,
        'explanation': item.get('explanation') if answered else None,
    }


def public_assessment(assessment: SparkAssessment) -> Dict[str, Any]:
    questions = assessment.questions if isinstance(assessment.questions, list) else []
    graded = [q for q in questions if isinstance(q, dict) and q.get('correct') is not None]
    correct = sum(1 for q in graded if q.get('correct') is True)
    return {
        'id': str(assessment.id),
        'mode': assessment.mode,
        'status': assessment.status,
        'title': assessment.title,
        'created_at': assessment.created_at.isoformat() if assessment.created_at else None,
        'completed_at': assessment.completed_at.isoformat() if assessment.completed_at else None,
        'total': len(questions),
        'answered': len(graded),
        'correct': correct,
        'counts_toward_mastery': bool(getattr(assessment, 'counts_toward_mastery', True)),
        'questions': [public_question(q) for q in questions],
    }


class AssessmentService:
    """Generate, administer, and grade session assessments."""

    @staticmethod
    def _unit_context(session) -> str:
        parts = []
        if getattr(session, 'unit', None) is not None:
            unit = session.unit
            parts.append(f"{getattr(unit, 'code', '')} {getattr(unit, 'name', '')}".strip())
        if session.unit_label:
            parts.append(session.unit_label)
        if session.target_goal:
            parts.append(f"Goal: {session.target_goal}")
        if session.learning_objective:
            parts.append(f"Objective: {session.learning_objective}")
        roadmap = session.roadmap if isinstance(session.roadmap, list) else []
        labels = [str(i.get('label')) for i in roadmap if isinstance(i, dict) and i.get('label')]
        if labels:
            parts.append('Roadmap: ' + '; '.join(labels))
        return ' | '.join(p for p in parts if p) or 'general study'

    @classmethod
    def generate(cls, session, count: int = 5, mode: Optional[str] = None,
                 topic: str = '', gateway: Optional[AIGateway] = None,
                 practice: bool = False) -> SparkAssessment:
        """Create a new assessment with server-side answer keys.

        `practice=True` produces revision material: answers still grade for
        feedback, but the quiz is excluded from mastery calculations.
        """
        count = max(MIN_QUESTIONS, min(int(count or 5), MAX_QUESTIONS))
        mode = (mode or session.assessment_mode or SparkAssessment.MODE_IMMEDIATE).strip().lower()
        if mode not in {c for c, _ in SparkAssessment.MODE_CHOICES}:
            mode = SparkAssessment.MODE_IMMEDIATE
        ai_gateway = gateway or AIGateway()
        focus = (topic or '').strip()[:300] or cls._unit_context(session)
        mcq_instruction = (
            ' Every question MUST be multiple choice with exactly 4 options; '
            'put the correct option text in "answer".' if mode == SparkAssessment.MODE_MCQ else '')
        prompt = (
            f'Create {count} study assessment questions for this student.\n'
            f'Context: {focus}\n'
            f'Question style: short, precise, exam-style.{mcq_instruction}\n'
            'Return ONLY a JSON object {"questions":[{"prompt":"...","options":["a","b","c","d"] (mcq only),'
            '"answer":"correct answer or option text","explanation":"one-line why"}]}. '
            'Vary difficulty; test understanding, not memorization of phrasing.'
        )
        llm_req = LLMRequest(
            task='general',
            messages=[ChatMessage(role='user', content=prompt)],
            system_instruction='You are an assessment writer for a study app. Respond ONLY with valid JSON.',
            temperature=0.4,
            max_tokens=2000,
        )
        parsed = _parse_json_object((ai_gateway.generate(llm_req).content or ''))
        raw_questions = parsed.get('questions') if isinstance(parsed, dict) else None
        if not isinstance(raw_questions, list) or not raw_questions:
            raise ValueError('The assessment generator returned no questions. Please try again.')
        questions = []
        for raw in raw_questions[:count]:
            if not isinstance(raw, dict) or not str(raw.get('prompt') or '').strip():
                continue
            item = {
                'qid': uuid.uuid4().hex[:12],
                'prompt': str(raw['prompt']).strip()[:600],
                'answer_key': str(raw.get('answer') or '').strip()[:600],
                'explanation': str(raw.get('explanation') or '').strip()[:300],
                'correct': None,
                'student_answer': None,
                'feedback': None,
                'graded_at': None,
            }
            options = raw.get('options')
            if mode == SparkAssessment.MODE_MCQ or (isinstance(options, list) and options):
                clean_opts = [str(o).strip()[:200] for o in options[:6] if str(o or '').strip()] \
                    if isinstance(options, list) else []
                if clean_opts:
                    item['options'] = clean_opts
                    if mode != SparkAssessment.MODE_MCQ:
                        item['mode'] = 'mcq_item'
            questions.append(item)
        if not questions:
            raise ValueError('The assessment generator returned no usable questions.')
        assessment = SparkAssessment.objects.create(
            session=session, user=session.user, mode=mode,
            title=(f'Practice — ' if practice else 'Assessment — ') + cls._unit_context(session)[:110],
            questions=questions,
            counts_toward_mastery=not practice)
        # A fresh assessment means the student is being tested — mark the
        # session's current focus as 'doing' if a roadmap exists.
        return assessment

    @classmethod
    def list_for_session(cls, session) -> List[SparkAssessment]:
        return list(session.assessments.all())

    @classmethod
    def get_owned(cls, user, assessment_id) -> Optional[SparkAssessment]:
        return SparkAssessment.objects.filter(pk=assessment_id, user=user).select_related('session').first()

    @classmethod
    def _find_question(cls, assessment: SparkAssessment, qid: str) -> Optional[Dict[str, Any]]:
        for item in assessment.questions if isinstance(assessment.questions, list) else []:
            if isinstance(item, dict) and item.get('qid') == qid:
                return item
        return None

    @classmethod
    def submit_answer(cls, assessment: SparkAssessment, qid: str, answer: str,
                      gateway: Optional[AIGateway] = None) -> Dict[str, Any]:
        """Grade one answer server-side; the only path that moves mastery."""
        if assessment.status != SparkAssessment.STATUS_ACTIVE:
            raise ValueError('This assessment is already completed.')
        answer = (answer or '').strip()[:1000]
        if not answer:
            raise ValueError('An answer is required.')
        item = cls._find_question(assessment, qid)
        if item is None:
            raise ValueError('Question not found in this assessment.')
        if item.get('correct') is not None:
            return {'already_graded': True, 'correct': bool(item['correct']),
                    'feedback': item.get('feedback'), 'explanation': item.get('explanation')}

        # MCQ grades deterministically: the student may answer with the option
        # text, its 1-based index, or its letter. Everything else goes through
        # the LLM grader against the stored answer key.
        correct: Optional[bool] = None
        feedback = ''
        options = item.get('options') or []
        if options:
            key = str(item.get('answer_key') or '').strip().lower()
            normalized = answer.strip().lower()
            if normalized == key:
                correct = True
            else:
                for idx, opt in enumerate(options):
                    opt_text = str(opt).strip().lower()
                    aliases = {str(idx + 1), chr(ord('a') + idx), opt_text}
                    if normalized in aliases:
                        correct = opt_text == key
                        break
        if correct is None:
            ai_gateway = gateway or AIGateway()
            grade_prompt = (
                f'Question: {item["prompt"]}\n'
                f'Reference answer: {item.get("answer_key", "")}'
                + (f'\nOptions: {json.dumps(options)}' if options else '')
                + f'\nStudent answer: {answer}'
            )
            llm_req = LLMRequest(
                task='general',
                messages=[ChatMessage(role='user', content=grade_prompt)],
                system_instruction=GRADING_PROMPT,
                temperature=0.0,
                max_tokens=300,
            )
            graded = _parse_json_object((ai_gateway.generate(llm_req).content or ''))
            if graded is None:
                raise ValueError('The grader did not return a verdict. Please try again.')
            correct = bool(graded.get('correct'))
            feedback = str(graded.get('feedback') or '').strip()[:300]

        item['student_answer'] = answer
        item['correct'] = correct
        item['feedback'] = feedback or (item.get('explanation') if correct else 'Not quite — review this section.')
        item['graded_at'] = timezone.now().isoformat()
        assessment.questions = [
            item if isinstance(q, dict) and q.get('qid') == qid else q
            for q in (assessment.questions if isinstance(assessment.questions, list) else [])
        ]
        all_answered = all(
            isinstance(q, dict) and q.get('correct') is not None for q in assessment.questions)
        if all_answered:
            assessment.status = SparkAssessment.STATUS_COMPLETED
            assessment.completed_at = timezone.now()
        assessment.save()

        cls._recompute_mastery(assessment.session)
        StudySessionService.recompute_progress(assessment.session)
        assessment.session.refresh_from_db(fields=['mastery_percent', 'progress_percent'])
        return {
            'correct': correct,
            'feedback': item.get('feedback'),
            'explanation': item.get('explanation'),
            'assessment_complete': assessment.status == SparkAssessment.STATUS_COMPLETED,
            'mastery_percent': assessment.session.mastery_percent,
            'progress_percent': assessment.session.progress_percent,
        }

    @classmethod
    def _recompute_mastery(cls, session) -> int:
        """Mastery = share of correctly graded items across ALL session assessments.

        Only server-graded items count; unanswered questions and self-reports
        contribute nothing.
        """
        graded = correct = 0
        for assessment in session.assessments.filter(counts_toward_mastery=True):
            for q in assessment.questions if isinstance(assessment.questions, list) else []:
                if isinstance(q, dict) and q.get('correct') is not None:
                    graded += 1
                    if q.get('correct'):
                        correct += 1
        mastery = round(correct / graded * 100) if graded else 0
        if session.mastery_percent != mastery:
            session.mastery_percent = mastery
            session.save(update_fields=['mastery_percent', 'updated_at'])
        return mastery
