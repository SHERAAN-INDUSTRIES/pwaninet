"""End-of-session study pack: performance report, practice questions, summary.

The report is computed deterministically from assessed data — never from
self-report. Practice quizzes grade for feedback but are excluded from
mastery via `counts_toward_mastery=False`.
"""
import logging
from typing import Any, Dict, Optional

from django.utils import timezone

from pwanimate.models import SparkStudyArtifact
from pwanimate.services.assessment import AssessmentService, public_assessment
from pwanimate.services.study_session import StudySessionService

logger = logging.getLogger(__name__)


def session_stats(session) -> Dict[str, Any]:
    """Deterministic performance snapshot for a study session."""
    assessments = [public_assessment(a) for a in session.assessments.all()]
    graded_assessments = [a for a in assessments if a['answered']]
    total_answered = sum(a['answered'] for a in assessments)
    total_correct = sum(a['correct'] for a in assessments)
    started = session.started_at
    ended = session.ended_at or timezone.now()
    duration_min = max(0, int((ended - started).total_seconds() // 60)) if started else 0
    roadmap = session.roadmap if isinstance(session.roadmap, list) else []
    done_sections = [i.get('label') for i in roadmap if isinstance(i, dict) and i.get('status') == 'done']
    open_sections = [i.get('label') for i in roadmap if isinstance(i, dict) and i.get('status') != 'done']
    return {
        'unit': session.unit_label or (getattr(session.unit, 'code', '') if session.unit_id else '') or 'General',
        'target_goal': session.target_goal or '',
        'duration_minutes': duration_min,
        'progress_percent': session.progress_percent or 0,
        'mastery_percent': session.mastery_percent or 0,
        'roadmap_coverage_percent': StudySessionService.roadmap_coverage(session) if roadmap else None,
        'roadmap_done': done_sections,
        'roadmap_open': open_sections,
        'assessments_total': len(assessments),
        'assessments_completed': sum(1 for a in assessments if a['status'] == 'completed'),
        'questions_answered': total_answered,
        'questions_correct': total_correct,
        'accuracy_percent': round(total_correct / total_answered * 100) if total_answered else None,
        'assessments': assessments,
    }


def build_report_markdown(session, stats: Dict[str, Any]) -> str:
    """Deterministic Markdown report — every number traces to graded data."""
    lines = [
        f"# Study Report — {stats['unit']}",
        '',
        f"**Target:** {stats['target_goal'] or session.learning_objective or session.current_topic or 'General study'}",
        f"**Time studied:** {stats['duration_minutes']} min  |  "
        f"**Progress:** {stats['progress_percent']}%  |  "
        f"**Mastery (tested):** {stats['mastery_percent']}%",
    ]
    if stats['roadmap_coverage_percent'] is not None:
        lines.append(f"**Roadmap coverage:** {stats['roadmap_coverage_percent']}%")
    lines += ['', '## Assessment performance', '']
    if stats['questions_answered']:
        lines.append(
            f"- Answered **{stats['questions_answered']}** questions across "
            f"**{stats['assessments_completed']}** completed assessment(s) — "
            f"**{stats['questions_correct']}** correct "
            f"({stats['accuracy_percent']}% accuracy).")
    else:
        lines.append('- No assessed questions answered yet — mastery stays at 0% until tests are completed.')
    for a in stats['assessments']:
        flag = ' *(practice — not counted toward mastery)*' if a.get('counts_toward_mastery') is False else ''
        lines.append(
            f"- {a['title'] or a['mode']}{flag}: {a['answered']}/{a['total']} answered, "
            f"{a['correct']} correct — {a['status']}")
    lines += ['', '## Roadmap', '']
    if stats['roadmap_done']:
        lines.append('**Completed:** ' + ', '.join(stats['roadmap_done']))
    if stats['roadmap_open']:
        lines.append('**Still open:** ' + ', '.join(stats['roadmap_open']))
    if not stats['roadmap_done'] and not stats['roadmap_open']:
        lines.append('- No roadmap sections defined yet.')
    lines += [
        '',
        '## Honest read',
        '',
        'Sections completed raise coverage; only correctly graded test answers raise '
        'mastery. Self-reported understanding is not counted.',
        f"Current standing: coverage and mastery combine into {stats['progress_percent']}% progress.",
    ]
    return '\n'.join(lines)


def generate_pack(session, gateway=None, include_practice: bool = True,
                  include_summary: bool = True) -> Dict[str, Any]:
    """Produce the end-of-session pack: report artifact + practice quiz + summary.

    Returns the created artifacts; safe to call repeatedly (each call adds a
    new report snapshot).
    """
    stats = session_stats(session)
    markdown = build_report_markdown(session, stats)
    report = SparkStudyArtifact.objects.create(
        session=session, user=session.user, kind=SparkStudyArtifact.KIND_REPORT,
        title=f"Study report — {stats['unit']}", payload=stats, markdown=markdown)

    practice = None
    if include_practice:
        try:
            assessment = AssessmentService.generate(
                session, count=8, practice=True, gateway=gateway)
            practice = SparkStudyArtifact.objects.create(
                session=session, user=session.user, kind=SparkStudyArtifact.KIND_PRACTICE,
                title=assessment.title,
                payload={'assessment_id': str(assessment.id), 'mode': assessment.mode,
                         'questions': len(assessment.questions or [])},
                markdown=f"Practice set generated: {len(assessment.questions or [])} questions ({assessment.mode}).")
        except Exception:
            logger.exception('Practice pack generation failed for session %s', session.id)

    summary = None
    if include_summary:
        try:
            result = StudySessionService.generate_session_summary(session=session, gateway=gateway)
            summary = SparkStudyArtifact.objects.create(
                session=session, user=session.user, kind=SparkStudyArtifact.KIND_SUMMARY,
                title=f"Session summary — {stats['unit']}",
                payload={'message_id': result.get('message_id')},
                markdown=result.get('summary_markdown') or '')
        except Exception:
            logger.exception('Session summary generation failed for session %s', session.id)

    return {'report': report, 'practice': practice, 'summary': summary, 'stats': stats}


def artifact_payload(artifact: SparkStudyArtifact) -> Dict[str, Any]:
    return {
        'id': str(artifact.id), 'kind': artifact.kind, 'title': artifact.title,
        'payload': artifact.payload or {}, 'markdown': artifact.markdown or '',
        'created_at': artifact.created_at.isoformat() if artifact.created_at else None,
    }
