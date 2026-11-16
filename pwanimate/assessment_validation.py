"""Database-free validation. Model-authored keys are never verification evidence."""
import json
import re
from fractions import Fraction

OUTCOMES = {'correct', 'incorrect', 'invalid', 'inconclusive', 'evaluation_failed'}
# Minimal deterministic reference fixture; prompt identity prevents borrowing a key
# for an unrelated generated question. Extend only with reviewed/computed items.
REFERENCES = {
    'det-diag-234-v1': {
        'prompt': 'Find the determinant of the diagonal matrix diag(2, 3, 4).',
        'expected': '24', 'absolute_tolerance': '0', 'relative_tolerance': '0',
        'concept_key': 'determinants', 'difficulty': 1, 'identity': 'det-diagonal-3x3',
        'misconceptions': {'9': 'adds_diagonal_instead_of_multiplying'},
    },
    'det-2x2-v1': {'prompt': 'Find the determinant of [[1, 2], [3, 4]].',
        'expected': '-2', 'difficulty': 1, 'identity': 'det-general-2x2', 'concept_key': 'determinants',
        'absolute_tolerance': '0', 'relative_tolerance': '0', 'misconceptions': {'2': 'reverses_determinant_sign'}},
    'det-triangular-v1': {'prompt': 'Find the determinant of [[2, 3, 1], [0, -1, 4], [0, 0, 5]].',
        'expected': '-10', 'difficulty': 2, 'identity': 'det-triangular-3x3', 'concept_key': 'determinants',
        'absolute_tolerance': '0', 'relative_tolerance': '0'},
    'det-row-swap-v1': {'prompt': 'A matrix has determinant 7. What is its determinant after swapping two rows once?',
        'expected': '-7', 'difficulty': 2, 'identity': 'det-row-swap', 'concept_key': 'determinants',
        'absolute_tolerance': '0', 'relative_tolerance': '0', 'misconceptions': {'7': 'ignores_row_swap_sign'}},
    'det-singular-v1': {'prompt': 'Find the determinant of [[2, 4], [1, 2]].',
        'expected': '0', 'difficulty': 2, 'identity': 'det-dependent-rows', 'concept_key': 'determinants',
        'absolute_tolerance': '0', 'relative_tolerance': '0'},
    'det-general-3x3-v1': {'prompt': 'Find the determinant of [[1, 2, 3], [0, 4, 5], [1, 0, 6]].',
        'expected': '22', 'difficulty': 3, 'identity': 'det-general-3x3', 'concept_key': 'determinants',
        'absolute_tolerance': '0', 'relative_tolerance': '0'},
}


def strict_object(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    try:
        value = json.loads(text, object_pairs_hook=pairs,
                           parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def verdict(text):
    value = strict_object(text)
    if (value is None or type(value.get('correct')) is not bool
            or set(value) - {'correct', 'feedback'}
            or not isinstance(value.get('feedback', ''), str)):
        return outcome('inconclusive', 'Invalid grading verdict')
    return outcome('correct' if value['correct'] else 'incorrect', value.get('feedback', ''))


def outcome(status, feedback=''):
    if status not in OUTCOMES: raise ValueError('Unknown outcome')
    return {'outcome': status, 'correct': True if status == 'correct' else False if status == 'incorrect' else None,
            'feedback': feedback}


def number(value):
    if not isinstance(value, str): raise ValueError('Numeric answer must be text')
    value = value.strip()
    if not value or len(value) > 128 or sum(c.isdigit() for c in value) > 64:
        raise ValueError('Numeric answer exceeds bounds')
    if re.fullmatch(r'[+-]?\d+/[+-]?\d+', value):
        numerator, denominator = map(int, value.split('/'))
        if denominator == 0: raise ValueError('Zero denominator')
        result = Fraction(numerator, denominator)
    elif re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', value):
        exponent = re.search(r'[eE]([+-]?\d+)$', value)
        if exponent and abs(int(exponent[1])) > 100: raise ValueError('Exponent exceeds bounds')
        result = Fraction(value)
    else:
        raise ValueError('Unsupported numeric syntax')
    if abs(result) > 10**100: raise ValueError('Numeric magnitude exceeds bounds')
    return result


def numerical(answer, expected, absolute_tolerance, relative_tolerance):
    try:
        key, absolute, relative = map(number, (expected, absolute_tolerance, relative_tolerance))
        if absolute < 0 or relative < 0: raise ValueError('Negative tolerance')
    except ValueError:
        return outcome('evaluation_failed', 'Invalid reference or tolerance')
    try: student = number(answer)
    except ValueError as exc: return outcome('invalid', str(exc))
    correct = abs(student-key) <= max(absolute, relative*abs(key))
    return outcome('correct' if correct else 'incorrect')


def validate_questions(raw, count, mode):
    if type(count) is not int or not 1 <= count <= 10: raise ValueError('Question count must be an integer from 1 to 10')
    if not isinstance(raw, list) or len(raw) != count: raise ValueError('Generated question count does not match request')
    result, seen = [], set()
    for index, question in enumerate(raw):
        if not isinstance(question, dict): raise ValueError('Invalid question')
        prompt = question.get('prompt')
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 600: raise ValueError('Invalid question text')
        qid = question.get('qid', f'q{index+1}')
        if not isinstance(qid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', qid) or qid in seen:
            raise ValueError('Invalid or duplicate question identifier')
        seen.add(qid)
        key = question.get('answer')
        if not isinstance(key, str) or not key.strip(): raise ValueError('Missing reference answer')
        item = {'qid': qid, 'prompt': prompt.strip(), 'answer_key': key.strip(),
                'explanation': question.get('explanation', ''), 'correct': None,
                'student_answer': None, 'feedback': None, 'graded_at': None,
                'validation_version': 1, 'certification_status': 'unverified_key'}
        if not isinstance(item['explanation'], str) or len(item['explanation']) > 600: raise ValueError('Invalid explanation')
        options = question.get('options')
        if mode == 'mcq' or options is not None:
            if not isinstance(options, list) or len(options) != 4: raise ValueError('MCQ requires exactly four options')
            labels, ids = [], []
            for i, option in enumerate(options):
                oid, label = (option.get('id'), option.get('label')) if isinstance(option, dict) else (chr(97+i), option)
                if (not isinstance(oid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', oid)
                        or not isinstance(label, str) or not label.strip() or len(label) > 200): raise ValueError('Invalid option')
                ids.append(oid); labels.append(label.strip())
            if len(set(ids)) != 4 or len({' '.join(x.casefold().split()) for x in labels}) != 4: raise ValueError('Duplicate options')
            matches = [i for i, label in enumerate(labels) if label == key.strip()]
            if len(matches) != 1: raise ValueError('MCQ key must match exactly one option')
            item.update(type='mcq', options=labels, option_ids=ids)
        else:
            number(key)
            item.update(type='numerical')
        ref = REFERENCES.get(question.get('reference_id')) if isinstance(question.get('reference_id'), str) else None
        if ref and item['prompt'] == ref['prompt']:
            if numerical(key, ref['expected'], '0', '0')['correct'] is not True: raise ValueError('Mathematically incorrect reference key')
            if item['type'] == 'mcq':
                # Reject mathematically equivalent distractors as well.
                if sum(numerical(x, ref['expected'], '0', '0')['correct'] is True for x in item['options']) != 1:
                    raise ValueError('Ambiguous mathematical options')
            item.update(certification_status='verified_reference', reference_id=question['reference_id'])
        result.append(item)
    return result


def grade(item, answer):
    if not isinstance(answer, str) or not answer.strip() or len(answer) > 1000: return outcome('invalid', 'An answer is required')
    if item.get('type') == 'mcq' or item.get('options'):
        options = item.get('options', [])
        if not isinstance(options, list) or not all(isinstance(x, str) for x in options): return outcome('evaluation_failed', 'Invalid stored options')
        ids = item.get('option_ids') or [chr(97+i) for i in range(len(options))]
        matches = [i for i, text in enumerate(options) if answer.strip().casefold() in {text.casefold(), str(i+1), ids[i].casefold()}]
        if len(matches) != 1: return outcome('invalid', 'Select one valid option')
        selected = options[matches[0]]
    elif item.get('type') == 'numerical':
        try: number(answer)
        except ValueError as exc: return outcome('invalid', str(exc))
        selected = answer
    else: return outcome('invalid', 'Unsupported answer format')
    ref = REFERENCES.get(item.get('reference_id')) if isinstance(item.get('reference_id'), str) else None
    if not ref or item.get('prompt') != ref['prompt']: return outcome('inconclusive', 'Reference key is not independently verified')
    if numerical(item.get('answer_key'), ref['expected'], '0', '0')['correct'] is not True: return outcome('evaluation_failed', 'Reference key mismatch')
    return numerical(selected, ref['expected'], ref['absolute_tolerance'], ref['relative_tolerance'])


def metrics(questions):
    items = [q if isinstance(q, dict) else {} for q in questions]
    correct = sum(type(q.get('correct')) is bool and q['correct'] for q in items)
    incorrect = sum(type(q.get('correct')) is bool and not q['correct'] for q in items)
    graded = correct+incorrect
    answered = sum(type(q.get('correct')) is bool or q.get('student_answer') is not None for q in items)
    inconclusive = sum(q.get('outcome') in {'inconclusive','evaluation_failed'} for q in items)
    return {'total': len(items), 'answered': answered, 'graded': graded, 'correct': correct, 'incorrect': incorrect,
            'unanswered': len(items)-answered, 'inconclusive': inconclusive,
            'pending': answered-graded-inconclusive,
            'completion_percent': round(answered/len(items)*100) if items else 0,
            'accuracy_percent': round(correct/graded*100) if graded else None,
            'mastery_state': 'insufficient_evidence'}
