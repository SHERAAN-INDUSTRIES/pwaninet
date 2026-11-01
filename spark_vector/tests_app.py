"""
In-container integration tests for the spark-vector HTTP surface.

Requires the baked model assets (runs inside the built image):

    docker run --rm -v "$PWD/spark_vector:/srv/spark_vector:ro" spark_vector:local \
        sh -c "cd /srv/spark_vector && python -m unittest discover -s . -p 'tests_*.py' -v"
"""

import asyncio
import math
import threading
import time
import unittest

import app as app_module
from fastapi.testclient import TestClient


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b))


class SparkVectorAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_ctx = TestClient(app_module.app)
        cls.client = cls.client_ctx.__enter__()  # triggers lifespan: loads the model

    @classmethod
    def tearDownClass(cls):
        cls.client_ctx.__exit__(None, None, None)

    def test_healthz_warm(self):
        body = self.client.get("/healthz").json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["warm"])
        self.assertEqual(body["model"], app_module.MODEL_NAME)
        self.assertIn("queue_depth", body)

    def test_models_catalog(self):
        body = self.client.get("/v1/models").json()
        self.assertEqual(body["data"][0]["id"], app_module.MODEL_NAME)

    def test_document_embedding_shape_and_norm(self):
        resp = self.client.post(
            "/v1/embeddings",
            json={"model": app_module.MODEL_NAME, "input": "The fee payment deadline is Friday."},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["object"], "list")
        self.assertEqual(body["model"], app_module.MODEL_NAME)
        item = body["data"][0]
        self.assertEqual(item["index"], 0)
        self.assertEqual(len(item["embedding"]), 768)
        norm = math.sqrt(sum(x * x for x in item["embedding"]))
        self.assertAlmostEqual(norm, 1.0, places=3)
        self.assertGreater(body["usage"]["prompt_tokens"], 0)

    def test_query_and_document_vectors_differ_for_same_text(self):
        text = "when is the fee payment deadline?"
        query = self.client.post(
            "/v1/embeddings",
            json={"model": app_module.MODEL_NAME, "input": text, "task_type": "RETRIEVAL_QUERY"},
        ).json()["data"][0]["embedding"]
        document = self.client.post(
            "/v1/embeddings",
            json={"model": app_module.MODEL_NAME, "input": text, "task_type": "RETRIEVAL_DOCUMENT"},
        ).json()["data"][0]["embedding"]
        self.assertLess(cosine(query, document), 0.99999, "e5 prefixes must change the vector")

    def test_semantic_ordering_paraphrase_beats_unrelated(self):
        doc = self.client.post(
            "/v1/embeddings",
            json={"model": app_module.MODEL_NAME, "input": "The last day to pay fees is Friday morning."},
        ).json()["data"][0]["embedding"]
        paraphrase = self.client.post(
            "/v1/embeddings",
            json={"model": app_module.MODEL_NAME, "input": "When do I need to settle my fees?",
                  "task_type": "RETRIEVAL_QUERY"},
        ).json()["data"][0]["embedding"]
        unrelated = self.client.post(
            "/v1/embeddings",
            json={"model": app_module.MODEL_NAME, "input": "The library closes at nine pm daily.",
                  "task_type": "RETRIEVAL_QUERY"},
        ).json()["data"][0]["embedding"]
        self.assertGreater(cosine(doc, paraphrase), cosine(doc, unrelated))

    def test_batch_and_ordering(self):
        inputs = [f"Document number {i} about campus life." for i in range(4)]
        body = self.client.post(
            "/v1/embeddings", json={"model": app_module.MODEL_NAME, "input": inputs}
        ).json()
        self.assertEqual([item["index"] for item in body["data"]], [0, 1, 2, 3])
        self.assertTrue(all(len(item["embedding"]) == 768 for item in body["data"]))

    def test_string_input_returns_single_entry(self):
        body = self.client.post(
            "/v1/embeddings", json={"model": app_module.MODEL_NAME, "input": "single"}
        ).json()
        self.assertEqual(len(body["data"]), 1)

    def test_model_mismatch_rejected(self):
        resp = self.client.post(
            "/v1/embeddings", json={"model": "text-embedding-ada-002", "input": "x"}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("serves", resp.json()["error"]["message"])

    def test_input_cap_enforced(self):
        resp = self.client.post(
            "/v1/embeddings",
            json={"model": app_module.MODEL_NAME, "input": ["x"] * (app_module.MAX_INPUTS + 1)},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Too many inputs", resp.json()["error"]["message"])

    def test_empty_input_rejected(self):
        resp = self.client.post(
            "/v1/embeddings", json={"model": app_module.MODEL_NAME, "input": []}
        )
        self.assertEqual(resp.status_code, 400)

    def test_unknown_task_type_rejected(self):
        resp = self.client.post(
            "/v1/embeddings",
            json={"model": app_module.MODEL_NAME, "input": "x", "task_type": "CLASSIFICATION"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("task_type", resp.json()["error"]["message"])

    def test_optional_bearer_token_enforced_when_configured(self):
        original = app_module.TOKEN
        try:
            app_module.TOKEN = "s3cret"
            denied = self.client.post(
                "/v1/embeddings", json={"model": app_module.MODEL_NAME, "input": "x"}
            )
            self.assertEqual(denied.status_code, 401)
            allowed = self.client.post(
                "/v1/embeddings",
                json={"model": app_module.MODEL_NAME, "input": "x"},
                headers={"Authorization": "Bearer s3cret"},
            )
            self.assertEqual(allowed.status_code, 200, allowed.text)
        finally:
            app_module.TOKEN = original


    def test_oversized_request_refused_with_413(self):
        """A request beyond the per-request token budget is refused, not queued:
        it must never hold the single inference slot for minutes."""
        budget = app_module.MAX_TOKENS_PER_REQUEST
        count = (budget // app_module.MAX_LENGTH) + 2
        huge = ["x" * 4000 for _ in range(count)]
        resp = self.client.post(
            "/v1/embeddings", json={"model": app_module.MODEL_NAME, "input": huge}
        )
        self.assertEqual(resp.status_code, 413, resp.text)
        self.assertIn("token budget", resp.json()["error"]["message"])

    def test_backlog_cap_returns_503_with_retry_after(self):
        """A saturated backlog is refused so callers back off (Celery retry)
        instead of piling more work onto a busy box."""
        original = app_module._admission._waiters["document"]
        try:
            app_module._admission._waiters["document"] = app_module.MAX_WAITERS
            resp = self.client.post(
                "/v1/embeddings",
                json={
                    "model": app_module.MODEL_NAME,
                    "input": "x",
                    "task_type": "RETRIEVAL_DOCUMENT",
                },
            )
            self.assertEqual(resp.status_code, 503, resp.text)
            self.assertEqual(resp.headers.get("Retry-After"), "5")
        finally:
            app_module._admission._waiters["document"] = original


class AdmissionControlTests(unittest.TestCase):
    """The admission layer: interactive queries first, bulk never starved,
    backlog capped. Async-native — a blocking wait here would stall the whole
    service."""

    @staticmethod
    def _worker(admission, lane, name, order):
        async def run():
            await admission.acquire(lane)
            order.append(name)
            admission.release()
        return run()

    def test_query_is_admitted_before_a_waiting_bulk_job(self):
        async def scenario():
            admission = app_module._Admission(concurrency=1, max_waiters=8)
            await admission.acquire("document")  # occupy the single slot
            order = []
            bulk = asyncio.create_task(
                self._worker(admission, "document", "bulk", order)
            )
            await asyncio.sleep(0.05)
            query = asyncio.create_task(
                self._worker(admission, "query", "query", order)
            )
            await asyncio.sleep(0.05)
            admission.release()  # free the slot
            await asyncio.gather(bulk, query)
            return order

        order = asyncio.run(scenario())
        self.assertEqual(order[0], "query")

    def test_bulk_proceeds_when_no_query_waits(self):
        async def scenario():
            admission = app_module._Admission(concurrency=1, max_waiters=8)
            await admission.acquire("document")
            order = []
            bulk = asyncio.create_task(
                self._worker(admission, "document", "bulk", order)
            )
            await asyncio.sleep(0.05)
            admission.release()
            await bulk
            return order

        order = asyncio.run(scenario())
        self.assertEqual(order, ["bulk"])

    def test_backlog_cap_refuses_new_work(self):
        async def scenario():
            admission = app_module._Admission(concurrency=1, max_waiters=1)
            await admission.acquire("document")
            refused = []

            async def worker():
                try:
                    await admission.acquire("document", timeout=1.0)
                except app_module._BacklogFull:
                    refused.append("full")

            first = asyncio.create_task(worker())
            await asyncio.sleep(0.05)  # occupy the single permitted waiter slot
            second = asyncio.create_task(worker())
            await asyncio.sleep(0.05)
            admission.release()
            await asyncio.gather(first, second)
            return refused

        refused = asyncio.run(scenario())
        self.assertIn("full", refused)

    def test_waiting_does_not_block_the_event_loop(self):
        """A waiting request must not stall the loop: another request has to
        complete while the first one waits for the slot."""
        async def scenario():
            admission = app_module._Admission(concurrency=1, max_waiters=8)
            await admission.acquire("document")  # hold the slot
            order = []

            async def waiter():
                await admission.acquire("query")
                order.append("query")
                admission.release()

            async def other():
                # Independent work must run while the waiter is parked.
                await asyncio.sleep(0.1)
                order.append("other")

            task = asyncio.create_task(waiter())
            await asyncio.sleep(0.02)
            await other()
            self.assertEqual(order, ["other"])  # ran while the waiter waited
            admission.release()
            await task

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
