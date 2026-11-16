"""Pure, provisional rules. Accuracy is observed performance, not a probability."""
import hashlib
import json
from datetime import timedelta
from django.conf import settings

DEFAULTS = {'min_questions': 5, 'min_contexts': 2, 'min_non_easy_correct': 2,
    'min_accuracy': 0.8, 'retention_days': 7, 'min_retention_questions': 2,
    'review_days': 30, 'recent_days': 90, 'regression_errors': 2, 'regression_window': 3}


def policy_config():
    overrides = getattr(settings, 'SALS_MASTERY_POLICY', {})
    if not isinstance(overrides, dict) or set(overrides) - set(DEFAULTS):
        raise ValueError('Invalid SALS_MASTERY_POLICY keys')
    policy = {**DEFAULTS, **overrides}
    for key, value in policy.items():
        if key == 'min_accuracy':
            if type(value) not in (int, float) or not 0.5 <= value <= 1:
                raise ValueError('min_accuracy must be between 0.5 and 1')
        elif type(value) is not int or not 1 <= value <= 365:
            raise ValueError('Policy counts/days must be positive bounded integers')
    if policy['min_questions'] < 3 or policy['min_contexts'] < 2 or policy['regression_errors'] > policy['regression_window'] \
            or policy['recent_days'] < policy['retention_days']:
        raise ValueError('Policy must retain independent question/context/retention safeguards')
    digest = hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()[:16]
    return policy, 'wp1-v1:' + digest


def evaluate(evidence, prerequisite_states, now, policy, *, was_mastered=False):
    """Input rows are validated server evidence; this function does not verify provenance."""
    observations = [e for e in evidence if e['usable'] and e['occurred_at'] <= now]
    decisive = [e for e in observations if e['verified'] and e['outcome'] in {'correct', 'incorrect'}]
    independent = sorted([e for e in decisive if e['independent']], key=lambda e: (e['occurred_at'], e['id']))
    latest = {e['identity']: e for e in independent}
    contexts = {e['context'] for e in independent}
    weighted_total = sum(e['difficulty'] for e in latest.values())
    accuracy = sum(e['difficulty'] for e in latest.values() if e['outcome'] == 'correct') / weighted_total if weighted_total else None
    correct = [e for e in independent if e['outcome'] == 'correct']
    anchor = min((e['occurred_at'] for e in correct), default=None)
    retention = [e for e in correct if e['purpose'] == 'retention' and anchor
        and e['delivered_at'] >= anchor + timedelta(days=policy['retention_days'])]
    latest_retention = max((e['occurred_at'] for e in retention), default=None)
    due = latest_retention + timedelta(days=policy['review_days']) if latest_retention else None
    latest_failure = max((e['occurred_at'] for e in independent if e['outcome'] == 'incorrect'), default=None)
    prereqs_ok = all(state == 'MASTERED' for state in prerequisite_states)
    recent = [e for e in independent if e['occurred_at'] >= now - timedelta(days=policy['recent_days'])]
    errors = sum(e['outcome'] == 'incorrect' for e in recent[-policy['regression_window']:])
    requirements = {
        'question_quantity': len(latest) >= policy['min_questions'],
        'context_variety': len(contexts) >= policy['min_contexts'],
        'difficulty': sum(e['outcome'] == 'correct' and e['difficulty'] >= 2 for e in latest.values()) >= policy['min_non_easy_correct'],
        'accuracy': accuracy is not None and accuracy >= policy['min_accuracy'],
        'prerequisites': prereqs_ok,
        'retention': len({e['identity'] for e in retention}) >= policy['min_retention_questions'],
        'no_later_gap': latest_failure is None or (latest_retention is not None and latest_retention >= latest_failure),
    }
    if not observations:
        state, reason = 'NOT_STARTED', 'no_usable_evidence'
    elif not decisive:
        state, reason = 'LEARNING', 'observations_or_unverified_attempts_only'
    elif errors >= policy['regression_errors']:
        state, reason = 'PRACTICING', 'recent_verified_gaps'
    elif all(requirements.values()):
        state, reason = ('REVIEW_DUE', 'scheduled_review_due') if now >= due else ('MASTERED', 'policy_satisfied')
    elif was_mastered and (not prereqs_ok or (latest_failure and latest_retention and latest_failure > latest_retention)):
        state, reason = 'REVIEW_DUE', 'prerequisite_or_later_performance_gap'
    else:
        state, reason = 'PRACTICING', 'insufficient_independent_retention_evidence'
    return {'state': state, 'reason': reason, 'requirements': requirements,
        'weighted_accuracy': round(accuracy, 6) if accuracy is not None else None,
        'accuracy_semantics': 'observed latest independent results per question family; not a mastery probability',
        'independent_questions': len(latest), 'contexts': len(contexts),
        'retention_questions': len({e['identity'] for e in retention}),
        'last_assessed_at': max((e['occurred_at'] for e in decisive), default=None),
        'last_reviewed_at': latest_retention, 'review_due_at': due,
        'misconceptions': sorted({code for e in latest.values() if e['outcome'] == 'incorrect' for code in e['misconceptions']}),
        'evidence_ids': [e['id'] for e in observations]}
