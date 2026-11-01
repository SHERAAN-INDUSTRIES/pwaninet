"""
Unit tests for spark-vector pure logic (prefixing, pooling, normalization).

Imports only embedding_logic (numpy) — runs on the host venv AND in-container.
"""

import unittest

import numpy as np

from embedding_logic import (
    PASSAGE_PREFIX,
    QUERY_PREFIX,
    apply_prefix,
    l2_normalize,
    mean_pool,
    normalize_task_type,
    prefix_texts,
)


class TaskTypeTests(unittest.TestCase):
    def test_query_aliases(self):
        for value in ("RETRIEVAL_QUERY", "retrieval_query", " query ", "Query"):
            self.assertEqual(normalize_task_type(value), "query")

    def test_document_aliases(self):
        for value in ("RETRIEVAL_DOCUMENT", "retrieval_document", "passage", "document", None, ""):
            self.assertEqual(normalize_task_type(value), "document")

    def test_unknown_task_type_raises(self):
        with self.assertRaises(ValueError):
            normalize_task_type("CLASSIFICATION")


class PrefixTests(unittest.TestCase):
    def test_query_prefix(self):
        self.assertEqual(apply_prefix("when is fees due?", "query"), "query: when is fees due?")

    def test_document_prefix(self):
        self.assertEqual(apply_prefix("fees due friday", "document"), "passage: fees due friday")

    def test_prefix_is_idempotent(self):
        # Literal query/passage inputs pass through untouched (spec §4).
        self.assertEqual(apply_prefix("query: already prefixed", "document"), "query: already prefixed")
        self.assertEqual(apply_prefix("passage: already prefixed", "query"), "passage: already prefixed")

    def test_prefix_texts_batch(self):
        out = prefix_texts(["a", "b"], "query")
        self.assertEqual(out, [QUERY_PREFIX + "a", QUERY_PREFIX + "b"])
        self.assertTrue(all(item.startswith(PASSAGE_PREFIX) for item in prefix_texts(["a"], "document")))


class PoolingTests(unittest.TestCase):
    def test_mean_pool_ignores_padding(self):
        hidden = np.array([
            [[1.0, 1.0], [3.0, 3.0], [100.0, 100.0]],  # third token is padding
            [[2.0, 4.0], [4.0, 8.0], [50.0, 50.0]],
        ])
        mask = np.array([[1, 1, 0], [1, 1, 1]])
        pooled = mean_pool(hidden, mask)
        np.testing.assert_allclose(pooled[0], [2.0, 2.0])              # mean of first two only
        np.testing.assert_allclose(pooled[1], [56 / 3, 62 / 3])        # mean of all three

    def test_l2_normalize_unit_rows(self):
        vectors = np.array([[3.0, 4.0], [0.0, 5.0]])
        normalized = l2_normalize(vectors)
        np.testing.assert_allclose(np.linalg.norm(normalized, axis=1), [1.0, 1.0], rtol=1e-6)
        np.testing.assert_allclose(normalized[0], [0.6, 0.8], rtol=1e-6)

    def test_l2_normalize_zero_vector_safe(self):
        normalized = l2_normalize(np.array([[0.0, 0.0]]))
        self.assertTrue(np.all(np.isfinite(normalized)))


if __name__ == "__main__":
    unittest.main()
