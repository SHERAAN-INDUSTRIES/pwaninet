from datetime import timedelta
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from pwanimate.mastery_policy import evaluate, policy_config, DEFAULTS


class PolicyTests(SimpleTestCase):
    def setUp(self):
        self.now = timezone.now()

    def row(self, identity, *, age=8, context='initial', purpose='practice', difficulty=2, correct=True):
        when = self.now - timedelta(days=age)
        return {'id': f'{identity}:{age}:{context}', 'identity': identity, 'context': context,
            'purpose': purpose, 'delivered_at': when, 'occurred_at': when, 'usable': True,
            'verified': True, 'independent': True, 'difficulty': difficulty,
            'outcome': 'correct' if correct else 'incorrect', 'misconceptions': []}

    def ready(self):
        return [self.row(str(i)) for i in range(5)] + [self.row(str(i), age=0, context='retention', purpose='retention') for i in range(2)]

    def test_policy_is_deterministic_and_never_calls_a_provider(self):
        first = evaluate(self.ready(), [], self.now, DEFAULTS)
        self.assertEqual(first, evaluate(self.ready(), [], self.now, DEFAULTS))
        self.assertEqual(first['state'], 'MASTERED')
        self.assertNotIn('probability', first)

    def test_prerequisite_review_or_regression_blocks_ready_dependent(self):
        for state in ['NOT_STARTED', 'LEARNING', 'PRACTICING', 'REVIEW_DUE']:
            self.assertNotEqual(evaluate(self.ready(), [state], self.now, DEFAULTS)['state'], 'MASTERED')
            self.assertEqual(evaluate(self.ready(), [state], self.now, DEFAULTS, was_mastered=True)['state'], 'REVIEW_DUE')

    def test_question_families_and_contexts_are_independent_requirements(self):
        rows = self.ready()
        for row in rows:
            row['identity'] = 'one-repeated-template'
        self.assertFalse(evaluate(rows, [], self.now, DEFAULTS)['requirements']['question_quantity'])
        rows = self.ready()
        for row in rows:
            row['context'] = 'one-context'
        self.assertFalse(evaluate(rows, [], self.now, DEFAULTS)['requirements']['context_variety'])

    def test_difficulty_and_accuracy_are_checked_separately(self):
        rows = self.ready()
        for row in rows:
            row['difficulty'] = 1
        self.assertFalse(evaluate(rows, [], self.now, DEFAULTS)['requirements']['difficulty'])
        rows = self.ready() + [self.row('3', age=0, context='failure', difficulty=3, correct=False)]
        self.assertFalse(evaluate(rows, [], self.now, DEFAULTS)['requirements']['accuracy'])

    def test_invalid_and_future_evidence_are_excluded(self):
        rows = self.ready()
        for row in rows:
            row['usable'] = False
        self.assertEqual(evaluate(rows, [], self.now, DEFAULTS)['state'], 'NOT_STARTED')
        self.assertEqual(evaluate([self.row('future', age=-1)], [], self.now, DEFAULTS)['state'], 'NOT_STARTED')

    def test_configuration_version_is_stable_and_changes_with_threshold(self):
        _, version = policy_config()
        self.assertEqual(version, policy_config()[1])
        with override_settings(SALS_MASTERY_POLICY={'min_questions': 6}):
            self.assertNotEqual(version, policy_config()[1])

    def test_all_reference_numerical_results_are_explicit_and_exact(self):
        from itertools import permutations
        from math import prod
        from pwanimate.assessment_validation import REFERENCES
        matrices = {'det-diag-234-v1': [[2,0,0],[0,3,0],[0,0,4]],
            'det-2x2-v1': [[1,2],[3,4]], 'det-triangular-v1': [[2,3,1],[0,-1,4],[0,0,5]],
            'det-row-swap-v1': [[0,1],[7,0]], 'det-singular-v1': [[2,4],[1,2]],
            'det-general-3x3-v1': [[1,2,3],[0,4,5],[1,0,6]]}
        for key, matrix in matrices.items():
            value = 0
            for permutation in permutations(range(len(matrix))):
                inversions = sum(permutation[i] > permutation[j] for i in range(len(matrix)) for j in range(i+1, len(matrix)))
                value += (-1)**inversions * prod(matrix[i][j] for i,j in enumerate(permutation))
            self.assertEqual(str(value), REFERENCES[key]['expected'], key)
