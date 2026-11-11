"""Structured Spark interactions: creation, validation, and exactly-once resolution.

One pending interaction per run at a time. Resolution is authoritative and
server-side: the embedded widget, the main composer, and any API client all
funnel through resolve(), which validates the answer against the stored
contract before the run is allowed to continue.
"""
import hashlib
import json
import re
import unicodedata
from django.db import transaction
from django.utils import timezone
from pwanimate.models import SparkAgentInteraction, SparkAgentRun


class InteractionError(ValueError):
    """A rejected interaction submission. `code` is a stable machine label."""

    def __init__(self, message, code='invalid'):
        super().__init__(message)
        self.code = code


def _option_key(label, index):
    slug = re.sub(r'[^a-z0-9]+', '-', unicodedata.normalize('NFKD', str(label)).encode('ascii', 'ignore').decode().lower()).strip('-')
    return (slug or f'option-{index + 1}')[:40]


def normalize_options(raw):
    """Coerce planner/client option lists into [{'id','label'}], bounded."""
    if not isinstance(raw, list):
        return []
    options, seen = [], set()
    for index, entry in enumerate(raw[:SparkAgentInteraction.MAX_OPTIONS]):
        if isinstance(entry, dict):
            label = str(entry.get('label') or entry.get('text') or '').strip()[:160]
            opt_id = str(entry.get('id') or '').strip()[:60]
        else:
            label = str(entry or '').strip()[:160]
            opt_id = ''
        if not label:
            continue
        opt_id = opt_id or _option_key(label, index)
        if opt_id in seen:
            opt_id = f'{opt_id}-{index + 1}'
        seen.add(opt_id)
        options.append({'id': opt_id, 'label': label})
    return options


INPUT_GROUP = 'group'
MAX_GROUP_QUESTIONS = 4


def normalize_group_questions(raw):
    """Coerce a planner question list into bounded group-question dicts.

    Each entry: {'id','prompt','input_type','options','required',
    'allow_custom_answer'}. Returns [] when fewer than two usable questions
    remain — a single question stays on the single-question path.
    """
    if not isinstance(raw, list):
        return []
    questions, seen = [], set()
    for index, entry in enumerate(raw[:MAX_GROUP_QUESTIONS]):
        if not isinstance(entry, dict):
            continue
        prompt = str(entry.get('question') or entry.get('prompt') or '').strip()[:600]
        if not prompt:
            continue
        qid = str(entry.get('id') or '').strip()[:40] or f'q{index + 1}'
        base, n = qid, 2
        while qid in seen:
            qid = f'{base}-{n}'
            n += 1
        seen.add(qid)
        itype = str(entry.get('question_type') or entry.get('input_type') or 'text').strip().lower()
        itype = {'single': INPUT_SINGLE, 'choice': INPUT_SINGLE,
                 'multi': INPUT_MULTI}.get(itype, itype)
        if itype not in {SparkAgentInteraction.INPUT_TEXT, SparkAgentInteraction.INPUT_SINGLE,
                         SparkAgentInteraction.INPUT_MULTI, SparkAgentInteraction.INPUT_CONFIRM}:
            itype = SparkAgentInteraction.INPUT_TEXT
        q_options = normalize_options(entry.get('question_options') or entry.get('options')) \
            if itype in {SparkAgentInteraction.INPUT_SINGLE, SparkAgentInteraction.INPUT_MULTI} else []
        if itype in {SparkAgentInteraction.INPUT_SINGLE, SparkAgentInteraction.INPUT_MULTI} and not q_options:
            itype = SparkAgentInteraction.INPUT_TEXT
        questions.append({
            'id': qid, 'prompt': prompt, 'input_type': itype,
            'options': q_options,
            'required': bool(entry.get('required', True)),
            'allow_custom_answer': bool(entry.get('allow_custom_answer', True)),
        })
    return questions if len(questions) >= 2 else []


def create_interaction(run, *, kind=SparkAgentInteraction.KIND_CLARIFICATION,
                       input_type=SparkAgentInteraction.INPUT_TEXT, prompt='Ask Spark.',
                       options=None, allow_custom_answer=True, required=True,
                       user=None, conversation=None, questions=None):
    """Create the run's pending interaction, retiring any older pending ones."""
    group = normalize_group_questions(questions)
    if group:
        input_type = INPUT_GROUP
        normalized = group
        prompt = 'Spark has a few questions for you.'
        allow_custom_answer = True
        required = True
    else:
        prompt = str(prompt or '').strip()[:2000] or 'Spark has a question.'
        normalized = normalize_options(options) if input_type in {
            SparkAgentInteraction.INPUT_SINGLE, SparkAgentInteraction.INPUT_MULTI} else []
        if input_type in {SparkAgentInteraction.INPUT_SINGLE, SparkAgentInteraction.INPUT_MULTI} and not normalized:
            input_type = SparkAgentInteraction.INPUT_TEXT
            allow_custom_answer = True
    expires_at = run.expires_at
    with transaction.atomic():
        SparkAgentInteraction.objects.filter(
            run=run, status=SparkAgentInteraction.STATUS_PENDING,
        ).exclude(prompt=prompt).update(status=SparkAgentInteraction.STATUS_CANCELLED,
                                      resolved_at=timezone.now())
        interaction, _ = SparkAgentInteraction.objects.update_or_create(
            run=run, prompt=prompt, status=SparkAgentInteraction.STATUS_PENDING,
            defaults={
                'user': user or run.user,
                'conversation': conversation,
                'kind': kind,
                'input_type': input_type,
                'options': normalized,
                'allow_custom_answer': bool(allow_custom_answer),
                'required': bool(required),
                'expires_at': expires_at,
                'version': 1,
            })
    return interaction


def _blank_answer(raw):
    """True when a raw per-question answer carries no usable content."""
    if raw is None:
        return True
    if isinstance(raw, str):
        return not raw.strip()
    if isinstance(raw, dict):
        if raw.get('confirmed') is not None:
            return False
        if raw.get('selected'):
            return False
        return not str(raw.get('text') or '').strip()
    return False


def _validate_question(itype, options, allow_custom_answer, required, raw):
    """Validate one question's raw answer. Returns the normalized dict, or
    None when the question was legitimately left unanswered (optional)."""
    if _blank_answer(raw):
        if required:
            raise InteractionError('Please answer every required question.', 'required')
        return None
    if isinstance(raw, str):
        raw = {'text': raw}
    if not isinstance(raw, dict):
        raise InteractionError('The answer format is not supported.', 'bad_type')

    text = raw.get('text')
    text = str(text).strip()[:SparkAgentInteraction.MAX_ANSWER_CHARS] if text is not None else ''
    selected = raw.get('selected')

    if itype == SparkAgentInteraction.INPUT_CONFIRM:
        confirmed = raw.get('confirmed', raw.get('decision'))
        if isinstance(confirmed, str):
            confirmed = confirmed.strip().lower() in {'confirm', 'approve', 'yes', 'true', '1'}
        if not isinstance(confirmed, bool):
            raise InteractionError('A confirmation needs an explicit decision.', 'bad_type')
        return {'confirmed': confirmed, 'text': text or None, 'selected': []}

    if itype in {SparkAgentInteraction.INPUT_SINGLE, SparkAgentInteraction.INPUT_MULTI}:
        option_ids = {o['id'] for o in (options or [])}
        if selected is None and text:
            # Free-text fallback is only valid when the contract allows it.
            if not allow_custom_answer:
                raise InteractionError('This question only accepts one of the listed options.', 'bad_option')
            return {'text': text, 'selected': [], 'custom': True}
        if not isinstance(selected, list):
            selected = [selected] if selected is not None else []
        selected = [str(s) for s in selected]
        if len(selected) != len(set(selected)):
            raise InteractionError('Duplicate selections are not allowed.', 'duplicate')
        unknown = [s for s in selected if s not in option_ids]
        if unknown:
            raise InteractionError('One or more selections are not valid options.', 'bad_option')
        if itype == SparkAgentInteraction.INPUT_SINGLE and len(selected) > 1:
            raise InteractionError('Choose exactly one option.', 'bad_option')
        if required and not selected and not (text and allow_custom_answer):
            raise InteractionError('This question requires an answer.', 'required')
        return {'text': text or None, 'selected': selected, 'custom': False}

    # Free text.
    if required and not text:
        raise InteractionError('This question requires an answer.', 'required')
    if text and len(text) > SparkAgentInteraction.MAX_ANSWER_CHARS:
        raise InteractionError('The answer is too long.', 'too_long')
    return {'text': text or None, 'selected': [], 'custom': False}


def _group_answer_text(questions, answers):
    """Compact readable rendering of a grouped answer for history/prompts."""
    parts = []
    for q in questions:
        ans = answers.get(q['id'])
        if not ans:
            continue
        if ans.get('confirmed') is True:
            value = 'Confirmed'
        elif ans.get('confirmed') is False:
            value = 'Declined'
        elif ans.get('selected'):
            labels = {o['id']: o['label'] for o in (q.get('options') or [])}
            value = ', '.join(labels.get(s, s) for s in ans['selected'])
        else:
            value = str(ans.get('text') or '').strip()
        if value:
            parts.append(f"{q['prompt']}: {value}")
    return '; '.join(parts)


def _validate_answer(interaction, raw):
    """Return the normalized answer dict or raise InteractionError."""
    itype = interaction.input_type
    if isinstance(raw, str):
        raw = {'text': raw}
    if not isinstance(raw, dict):
        raise InteractionError('The answer format is not supported.', 'bad_type')

    if itype == INPUT_GROUP:
        answers = raw.get('answers')
        if not isinstance(answers, dict):
            raise InteractionError('A grouped answer must map each question to its answer.', 'bad_type')
        questions = list(interaction.options or [])
        known = {q.get('id') for q in questions if isinstance(q, dict)}
        unknown = [k for k in answers if k not in known]
        if unknown:
            raise InteractionError('The answer references an unknown question.', 'bad_option')
        kept = {}
        for q in questions:
            qid = q.get('id')
            validated = _validate_question(
                q.get('input_type') or SparkAgentInteraction.INPUT_TEXT,
                q.get('options') or [],
                bool(q.get('allow_custom_answer', True)),
                bool(q.get('required', True)),
                answers.get(qid))
            if validated is not None:
                kept[qid] = validated
        if not kept:
            raise InteractionError('Please answer at least one question.', 'required')
        return {'answers': kept, 'text': _group_answer_text(questions, kept),
                'selected': []}

    return _validate_question(
        itype, interaction.options or [], interaction.allow_custom_answer,
        interaction.required, raw)


def resolve(run, interaction_id, raw_answer, *, submitted_via='embedded'):
    """Validate and record an answer exactly once. Returns the interaction.

    The run row is locked for the check-and-set so concurrent submissions
    cannot both resolve the same interaction or both resume the run.
    """
    with transaction.atomic():
        locked_run = SparkAgentRun.objects.select_for_update().get(pk=run.pk)
        interaction = SparkAgentInteraction.objects.select_for_update().filter(
            pk=interaction_id, run=locked_run, user=locked_run.user,
        ).first()
        if interaction is None:
            raise InteractionError('This question is unavailable.', 'not_found')
        if interaction.status != SparkAgentInteraction.STATUS_PENDING:
            raise InteractionError('This question has already been answered.', 'resolved')
        if interaction.expires_at <= timezone.now() or locked_run.expires_at <= timezone.now():
            interaction.status = SparkAgentInteraction.STATUS_EXPIRED
            interaction.resolved_at = timezone.now()
            interaction.save(update_fields=['status', 'resolved_at'])
            raise InteractionError('This question expired. Ask Spark again to get a fresh answer.', 'expired')
        answer = _validate_answer(interaction, raw_answer)
        interaction.answer = answer
        interaction.status = SparkAgentInteraction.STATUS_ANSWERED
        interaction.submitted_via = submitted_via[:32]
        interaction.resolved_at = timezone.now()
        interaction.version = interaction.version + 1
        interaction.save(update_fields=['answer', 'status', 'submitted_via', 'resolved_at', 'version'])
        return interaction


def pending_for_run(run):
    return SparkAgentInteraction.objects.filter(
        run=run, status=SparkAgentInteraction.STATUS_PENDING,
    ).order_by('-created_at').first()


def answer_text(interaction):
    """Human-readable form of a resolved answer, for history and prompts."""
    answer = interaction.answer or {}
    if answer.get('confirmed') is True:
        return 'Confirmed.'
    if answer.get('confirmed') is False:
        return 'Declined.'
    if answer.get('selected'):
        labels = {o['id']: o['label'] for o in (interaction.options or [])}
        return ', '.join(labels.get(s, s) for s in answer['selected'])
    return str(answer.get('text') or '').strip()


def answer_suffix(interaction):
    """The query suffix appended to the resumed request, as before."""
    return '\n\nStudent clarification: ' + (answer_text(interaction) or '')[:1000]
