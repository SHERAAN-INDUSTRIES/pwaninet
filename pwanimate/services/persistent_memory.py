"""Bounded owner-scoped retrieval and conservative, zero-provider-cost extraction.

Only explicit first-person statements in the academic vocabulary below are
eligible for automatic storage. Arbitrary facts still use owner approval.
"""
import hashlib
import logging
import re
from dataclasses import dataclass

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from pwanimate.models import (
    PwanimateMessage, SparkMemoryExtraction, SparkMemoryPreference, SparkPersonalMemory,
)

logger = logging.getLogger(__name__)
TOPICS = {
    'calculus': ['substitution', 'integration by parts', 'integration', 'differentiation', 'derivatives', 'limits'],
    'algebra': ['matrices', 'determinants', 'linear equations', 'quadratic equations', 'linear algebra'],
    'physics': ['mechanics', 'kinematics', 'thermodynamics', 'electromagnetism', 'quantum mechanics'],
    'computer science': ['programming', 'algorithms', 'data structures', 'database normalization', 'databases', 'recursion'],
    'mathematics': ['probability', 'statistics', 'geometry', 'trigonometry'],
    'chemistry': ['organic chemistry', 'stoichiometry'],
    'biology': ['genetics', 'cell biology'],
}
SUBJECTS = {topic: subject for subject, topics in TOPICS.items() for topic in [subject, *topics]}
STOP = set('a an the i my me you your what which should topic topics revise revision study studying learn learning please spark with about have for and but that this understand struggle'.split())


def normalize(text):
    return ' '.join(str(text).split()).strip()


def fingerprint(text):
    return hashlib.sha256(normalize(text).casefold().encode()).hexdigest()


def terms(text):
    return set(re.findall(r'[a-z0-9]{3,}', str(text).lower()[:2000])) - STOP


@dataclass(frozen=True)
class Candidate:
    category: str
    key: str
    text: str
    importance: float = .7
    confidence: float = 1.0


def candidates(text):
    """Full-clause grammar: quoted instructions/medical content never match.

    Strength/difficulty is self-reported, never inferred from an answer or mark.
    Repeated statements about the same topic share a key for supersession.
    """
    text = re.sub(r'^\s*spark[,!:]\s*', '', text.replace('’', "'"), flags=re.I)
    # A clause inside a pasted document or quoted example is not a self-report.
    # When attribution is ambiguous, skip the whole message rather than infer.
    if re.search(r'[\x22`<>:]|\b(?:document|quote|quoted|example|transcript|prompt|instructions?|pretend|hypothetical|suppose|password)\b', text, re.I):
        return []
    result = []
    for clause in re.split(r'[.!?;\n]|,?\s+but\s+|,?\s+and\s+', text, flags=re.I):
        clause = re.sub(r'^(?:actually[, ]+|now\s+)', '', clause.strip(' ,'), flags=re.I)
        match = re.fullmatch(
            r"I\s+(?:(?:am|'m) studying|study|want to learn|understand|struggle with|"
            r"no longer struggle with|(?:have )?mastered|completed|finished)\s+([a-z ]{3,70})", clause, re.I)
        if not match:
            # "I'm studying" has no space after I.
            match = re.fullmatch(r"I'm studying\s+([a-z ]{3,70})", clause, re.I)
        if not match:
            continue
        topic = match[1].strip().lower()
        if topic not in SUBJECTS:
            continue
        verb = clause.lower()
        subject = SUBJECTS[topic]
        if 'struggle with' in verb and 'no longer' not in verb:
            state, category = 'reports difficulty with', 'learning'
        elif any(v in verb for v in ('understand', 'mastered', 'no longer')):
            state, category = 'reports understanding', 'learning'
        elif 'completed' in verb or 'finished' in verb:
            state, category = 'reports completing study of', 'episodic'
        elif 'want to learn' in verb:
            state, category = 'wants to learn', 'semantic'
        else:
            state, category = 'is studying', 'semantic'
        key = ('ability' if category == 'learning' else 'goal' if state == 'wants to learn' else category) + ':' + topic
        result.append(Candidate(category, key, f'Student {state} {topic} ({subject}).'))
    return result[:8]


class PersistentMemoryService:
    @classmethod
    def retrieve(cls, user, query, limit=8, *, token_budget=None):
        """Use a local PostgreSQL deadline without changing the caller's session."""
        from pwanimate.services.recall_intent import is_recent_learning_recall
        if not user or not getattr(user, 'is_authenticated', False) or not (terms(query) or is_recent_learning_recall(query)):
            return []
        try:
            with transaction.atomic():
                if connection.vendor == 'postgresql':
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT current_setting('statement_timeout')")
                        previous_timeout = cursor.fetchone()[0]
                        timeout = max(10, min(int(getattr(settings, 'SPARK_MEMORY_SQL_TIMEOUT_MS', 150)), 1000))
                        cursor.execute("SELECT set_config('statement_timeout', %s, true)", [f'{timeout}ms'])
                result = cls._retrieve(user, query, limit, token_budget=token_budget)
                if connection.vendor == 'postgresql':
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT set_config('statement_timeout', %s, true)", [previous_timeout])
                return result
        except Exception:
            logger.exception('Spark memory retrieval failed; continuing without memory')
            return []

    @classmethod
    def _retrieve(cls, user, query, limit=8, *, token_budget=None):
        """Never searches raw messages, legacy vectors, other owners or inactive facts."""
        if not user or not getattr(user, 'is_authenticated', False):
            return []
        from pwanimate.services.personal_memory import enabled, memory_scope
        try:
            pref = SparkMemoryPreference.objects.filter(user=user).first()
            if pref is None or not enabled(pref):
                return []
            from pwanimate.services.recall_intent import is_recent_learning_recall
            recent_learning = is_recent_learning_recall(query)
            query_terms = terms(query)
            if not query_terms and not recent_learning:
                return []
            limit = max(0, min(int(limit), int(getattr(settings, 'SPARK_MEMORY_MAX_RESULTS', 12)), 12))
            budget = max(0, min(int(token_budget if token_budget is not None else
                getattr(settings, 'SPARK_MEMORY_TOKEN_BUDGET', 600)), 2000))
            if not limit or not budget:
                return []
            base = SparkPersonalMemory.objects.filter(user=user, status='approved')
            # Bound both SQL result size and the number/length of lexical predicates.
            lexical = Q(pk__isnull=True)
            for term in sorted(query_terms)[:16]:
                lexical |= Q(text__icontains=term)
            if recent_learning:
                from django.db.models.functions import Coalesce
                # Saved learning events only: do not substitute response
                # preferences or scan raw chat for a generic last-session ask.
                records = list(base.filter(Q(category__in=['learning', 'episodic']) |
                    Q(purpose='academic_learning', memory_key__startswith='semantic:')).filter(
                    Q(source_message__isnull=True) | Q(source_message__conversation__user=user)
                ).annotate(recalled_at=Coalesce('source_message__created_at', 'created_at'))
                    .order_by('-recalled_at', '-pk')[:50])
            else:
                records = list(base.filter(lexical).order_by('-updated_at')[:200])
            distances = {}
            # Opt-in only: no default network call. Adapter must use a local provider
            # with a bounded timeout. Failure always leaves lexical recall available.
            if not recent_learning and getattr(settings, 'SPARK_MEMORY_VECTOR_RETRIEVAL', False):
                try:
                    from pgvector.django import CosineDistance
                    from pwanimate.ai.embeddings.factory import get_embedding_provider
                    provider = get_embedding_provider()
                    vector = provider.embed_query(str(query)[:2000])
                    if len(vector) == 768:
                        semantic = base.filter(embedding_model=provider.get_model_name(), embedding__isnull=False).annotate(
                            distance=CosineDistance('embedding', vector)).filter(distance__lt=.35).order_by('distance')[:limit]
                        for fact in semantic:
                            distances[fact.pk] = 1 - float(fact.distance)
                            if all(f.pk != fact.pk for f in records):
                                records.append(fact)
                except DatabaseError:
                    raise  # Roll back before any more SQL, including timeout restoration.
                except Exception:
                    logger.warning('Spark memory vector retrieval unavailable', exc_info=True)
            now = timezone.now()
            ranked = []
            for index, fact in enumerate(records):
                overlap = len(query_terms & terms(fact.text))
                relevance = max(overlap / max(1, len(query_terms)), distances.get(fact.pk, 0))
                if not relevance and not recent_learning:
                    continue
                freshness = 1 / (1 + max(0, (now - fact.updated_at).days) / 90)
                score = .7 * relevance + .15 * fact.importance + .1 * fact.confidence + .05 * freshness
                if recent_learning:
                    score = 1 - index / (len(records) + 1)
                ranked.append((score, fact))
            result, used = [], 0
            for score, fact in sorted(ranked, key=lambda pair: (-pair[0], str(pair[1].pk))):
                content = fact.text[:1000]
                cost = (len(content.encode('utf-8')) + 3) // 4 + 100
                if used + cost > budget:
                    continue
                result.append({'memory_id': str(fact.pk), 'content': content,
                    'scope': memory_scope(fact), 'category': fact.category, 'confidence': fact.confidence,
                    'message_id': fact.source_message_id,
                    'conversation_id': str(fact.source_conversation_id) if fact.source_conversation_id else None,
                    'created_at': fact.created_at.isoformat(), 'score': round(score, 4),
                    **({'source_created_at': fact.recalled_at.isoformat()} if recent_learning else {}),
                    'match_type': 'recent_learning_fact' if recent_learning else 'persistent_fact', 'conversation_title': 'Student memory', 'role': 'user'})
                used += cost
                if len(result) >= limit:
                    break
            # No cache/index outside the owner-filtered source of truth.
            SparkPersonalMemory.objects.filter(user=user, pk__in=[r['memory_id'] for r in result], status='approved').update(last_accessed_at=now)
            return result
        except DatabaseError:
            # The enclosing atomic block must roll back a failed SQL transaction
            # before retrieve() handles the optional-memory failure.
            raise
        except Exception:
            logger.exception('Spark memory retrieval failed; continuing without memory')
            return []

    @classmethod
    def extract(cls, assistant_id, revision):
        """Receipt and facts commit together, serialized with consent and deletion."""
        assistant = PwanimateMessage.objects.select_related('conversation').filter(pk=assistant_id, role='assistant').first()
        if not assistant or assistant.task in {'agent_pause', 'agent_interim', 'teaching_pause'}:
            return 0
        with transaction.atomic():
            from pwanimate.services.personal_memory import enabled
            pref = SparkMemoryPreference.objects.select_for_update().filter(user_id=assistant.conversation.user_id).first()
            if not pref or not enabled(pref) or not pref.automatic_extraction or pref.revision != revision or not pref.automatic_enabled_at:
                return 0
            previous = PwanimateMessage.objects.filter(conversation=assistant.conversation,
                role='assistant', id__lt=assistant.id).exclude(task__in=['agent_pause', 'agent_interim', 'teaching_pause']).order_by('-id').first()
            sources = PwanimateMessage.objects.filter(conversation=assistant.conversation, role='user',
                id__lt=assistant.id, created_at__gte=pref.automatic_enabled_at)
            if previous:
                sources = sources.filter(id__gt=previous.id)
            if pref.reset_at:
                sources = sources.filter(created_at__gt=pref.reset_at)
            count = 0
            for source in sources.order_by('id')[:20]:
                _, created = SparkMemoryExtraction.objects.get_or_create(source_message=source,
                    defaults={'completed_message': assistant, 'consent_revision': revision})
                if not created:
                    continue
                for candidate in candidates(source.content[:4000]):
                    cutoff = pref.extraction_cutoffs.get(candidate.key)
                    if cutoff and source.created_at <= parse_datetime(cutoff):
                        continue  # A deleted topic cannot return from older queued work.
                    digest = fingerprint(candidate.text)
                    facts = SparkPersonalMemory.objects.filter(user_id=pref.user_id, status='approved')
                    if facts.filter(Q(fingerprint=digest) | Q(text=candidate.text)).exists():
                        continue
                    if SparkPersonalMemory.objects.filter(user_id=pref.user_id,
                            memory_key=candidate.key, automatic=False, status__in=['approved','proposed']).exists():
                        continue  # Do not overwrite an owner correction awaiting review.
                    if SparkPersonalMemory.objects.filter(user_id=pref.user_id,
                            memory_key=candidate.key, status__in=['revoked', 'rejected']).exists():
                        continue  # Topic-level rejection requires fresh owner review.
                    old = list(facts.filter(memory_key=candidate.key, automatic=True))
                    # An older delayed worker must never reverse a newer observation.
                    if any(f.source_message_id and f.source_message_id > source.id for f in old):
                        continue
                    fact = SparkPersonalMemory.objects.create(user_id=pref.user_id, text=candidate.text,
                        category=candidate.category, purpose='academic_learning', memory_key=candidate.key, fingerprint=digest,
                        confidence=candidate.confidence, importance=candidate.importance,
                        status='approved', automatic=True, consent_revision=revision,
                        source_message=source, source_conversation=source.conversation, decided_at=timezone.now())
                    for stale in old:
                        stale.status = 'superseded'
                        stale.superseded_by = fact
                        stale.version += 1
                        stale.save(update_fields=['status', 'superseded_by', 'version', 'updated_at'])
                    count += 1
            return count


def queue_extraction(assistant):
    """Persist delivery intent in the caller's message transaction."""
    from pwanimate.services.memory_dispatch import schedule
    return schedule(assistant)
