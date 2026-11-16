"""Bounded, read-only greeting context. Never recalls transcripts or starts work."""
import re
from django.utils import timezone
from pwanimate.models import (SparkAcademicTrackingPreference, SparkMemoryPreference,
    SparkPersonalMemory, SparkTeachingTurn)

_GREETING = re.compile(
    r"(?:(?:wagwan|hi|hello|hey|hiya|howdy|habari|sasa|mambo|niaje|jambo)"
    r"(?:[ ,]+(?:spark|there|friend|chief))?"
    r"(?:[ ,!.]+(?:how are you(?: doing)?|how.s life|what.s up))?"
    r"|how are you(?: doing)?|good (?:morning|afternoon|evening)(?:[ ,]+spark)?)"
    r"[ .!?]*", re.I)


def is_greeting(query):
    # Strip only trailing emoji/punctuation, never substantive mixed requests.
    text = str(query or '').strip()
    import unicodedata
    while text and (text[-1].isspace() or unicodedata.category(text[-1]) in
                    {'So', 'Sk', 'Mn', 'Cf', 'Po'}):
        text = text[:-1].rstrip()
    return bool(_GREETING.fullmatch(text) or re.fullmatch(
        r'(?:thanks|thank you|asante(?: sana)?)(?:[ ,]+(?:chief|spark|friend))?', text, re.I))


def greeting_context(user):
    if not user or not user.is_authenticated:
        return [], []
    from pwanimate.services.personal_memory import enabled
    pref = SparkMemoryPreference.objects.filter(user=user).first()
    preferences = []
    if pref and enabled(pref):
        facts = SparkPersonalMemory.objects.filter(user=user, status='approved',
            memory_key__startswith='preference:').order_by('-updated_at')[:3]
        preferences = [{'content': f.text[:300], 'scope':'personal', 'category':f.category,
            'memory_id':str(f.pk), 'match_type':'approved_preference'} for f in facts]
    academic = SparkAcademicTrackingPreference.objects.filter(user=user).first()
    if not academic or academic.mode == 'disabled':
        return preferences, []
    # Inspect a bounded set of actual finalized activity, not merely session rows.
    turns = SparkTeachingTurn.objects.filter(state__session__user=user,
        state__session__status__in=['active','paused'],
        state__session__learning_record__user=user,
        state__session__learning_record__consent_revision=academic.revision,
        state__session__learning_record__authorized_at__isnull=False,
        completed_at__gte=timezone.now()-timezone.timedelta(days=30),
        completed_message__isnull=False).select_related(
            'state__session__learning_record__enrollment__user',
            'state__session__learning_record__enrollment__academic_unit',
            'state__session__learning_record__enrollment__semester__academic_year',
            'state__session__unit') .order_by('-completed_at')[:20]
    from pwanimate.services.unit_learning import UnitLearningService
    candidates = {}
    for turn in turns:
        session = turn.state.session
        if UnitLearningService.eligible(session.learning_record.enrollment):
            candidates.setdefault(session.pk, (session, turn))
        if len(candidates) > 1:
            return preferences, []  # No arbitrary cross-session suggestion.
    if not candidates:
        return preferences, []
    session, turn = next(iter(candidates.values()))
    return preferences, [{'recent_session': {
        'session_id':str(session.pk), 'unit':session.unit_label or session.unit.name,
        'status':session.status, 'topic':str((turn.payload.get('teaching') or {}).get('objective',''))[:180],
        'recorded_activity':turn.activity_kind, 'completed_at':turn.completed_at.isoformat(),
        'read_only':True, 'grants_write_authorization':False,
        'evidence_semantics':'Completed exposure/explanation is not verified mastery.'}}]
