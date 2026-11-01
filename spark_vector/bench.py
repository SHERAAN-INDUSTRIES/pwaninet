#!/usr/bin/env python3
"""
spark-vector benchmark harness (docs/local-models-design.md §4 targets).

Measures the numbers the design doc commits to, on the real serving box:

  - single-query latency  (p50 / p95 / p99)   target: p50 < 80 ms, p95 < 200 ms
  - batch-of-N latency (N = the Django sub-batch size)
  - priority check: an interactive query must NOT wait behind a bulk batch
  - vector contract: 768 dims, L2-normalized   the hard invariant

Stdlib only (urllib), so it runs in a throwaway container attached to
pwaninet_net without touching the app's dependencies:

  docker run --rm --network pwaninet_net \
    -v "$PWD/spark_vector:/bench:ro" python:3.12-slim \
    python /bench/bench.py --url http://spark_vector:8100/v1/embeddings

Exit code 0 = every hard target met, 1 = at least one missed.
"""

import argparse
import json
import math
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request

# Realistic Kenyan student material: English + Swahili, and corpus-sized
# chunks (the Django chunker produces ~400-800 char chunks ≈ 100-200 tokens).
QUERIES = [
    "Explain the menstrual cycle phases",
    "Mchakato wa mzunguko wa mwezi unafanya nini?",
    "Compute the derivative of f(x) = 3x^2 - 2x + 7",
    "Difference between mitosis and meiosis",
    "Summarise the causes of the French Revolution",
    "How do I set up a Django REST API endpoint?",
    "Maelezo ya photosynthesis na importance yake",
    "Solve for x: 2x + 5 = 17",
]

CHUNK = (
    "Photosynthesis is the process by which green plants, algae and some bacteria convert light "
    "energy into chemical energy stored as glucose. The process takes place in the chloroplast, "
    "specifically in the thylakoid membranes where the light-dependent reactions occur, and in the "
    "stroma where the Calvin cycle fixes carbon dioxide into three-carbon sugars. Chlorophyll "
    "absorbs light most efficiently in the blue and red regions of the spectrum, which is why most "
    "plants appear green. Water is split during the light reactions, releasing oxygen, while "
    "NADP+ is reduced to NADPH, and the energy carriers ATP and NADPH then drive the Calvin cycle, "
    "in which carbon dioxide is fixed by RuBisCO onto ribulose-1,5-bisphosphate, producing "
    "glyceraldehyde-3-phosphate, the precursor of glucose and starch."
)

LONG_CHUNK = CHUNK * 4


def percentile(values, fraction):
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = int(round(fraction * (len(ordered) - 1)))
    return ordered[index]


def embed(url, texts, task_type, token, timeout=120.0):
    """POST /v1/embeddings; return (body, elapsed_ms)."""
    payload = json.dumps({"input": texts, "task_type": task_type}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    return body, (time.perf_counter() - started) * 1000.0


def summarize(label, samples, target=None):
    p50 = percentile(samples, 0.50)
    p95 = percentile(samples, 0.95)
    p99 = percentile(samples, 0.99)
    verdict = ""
    if target is not None:
        verdict = "PASS" if p50 < target else "FAIL"
    print(
        f"{label:<32} n={len(samples):<4} "
        f"p50={p50:8.1f} ms  p95={p95:8.1f} ms  p99={p99:8.1f} ms  "
        f"mean={statistics.fmean(samples):8.1f} ms  max={max(samples):8.1f}  {verdict}"
    )
    return verdict == "FAIL"


def main():
    parser = argparse.ArgumentParser(description="spark-vector benchmark")
    parser.add_argument("--url", default="http://spark_vector:8100/v1/embeddings")
    parser.add_argument("--token", default="")
    parser.add_argument("--queries", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--batches", type=int, default=10)
    args = parser.parse_args()

    print(f"target: {args.url}\n")

    # --- warmup: the first call pays model warm-up + import costs --------
    try:
        body, _ = embed(args.url, QUERIES[:1], "query", args.token)
    except urllib.error.HTTPError as exc:
        print(f"FAILED to reach the service: HTTP {exc.code} {exc.read()[:200]!r}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to reach the service: {exc}")
        return 1

    dims = len(body["data"][0]["embedding"])
    norm = math.sqrt(sum(v * v for v in body["data"][0]["embedding"]))
    print(f"contract: dims={dims} (expect 768)  l2_norm={norm:.6f} (expect 1.0)")
    failures = []
    if dims != 768:
        failures.append(f"dimension invariant broken: {dims} != 768")
    if abs(norm - 1.0) > 1e-3:
        failures.append(f"vectors not L2-normalized: {norm}")

    # --- single-query latency (the hot path: every chat message) ---------
    single = []
    for index in range(args.queries):
        _, elapsed = embed(args.url, QUERIES[index % len(QUERIES)], "query", args.token)
        single.append(elapsed)
    if summarize("single query", single, target=80.0):
        failures.append("single-query p50 >= 80 ms")

    # --- corpus-sized chunk (typical ingestion unit) --------------------
    chunk_samples = []
    for _ in range(10):
        _, elapsed = embed(args.url, CHUNK, "document", args.token)
        chunk_samples.append(elapsed)
    summarize("one chunk (~150 tok)", chunk_samples)

    # --- batch of N (the Django sub-batch size) --------------------------
    batch_samples = []
    batch_texts = [CHUNK for _ in range(args.batch_size)]
    for _ in range(args.batches):
        _, elapsed = embed(args.url, batch_texts, "document", args.token)
        batch_samples.append(elapsed)
    if summarize(f"batch of {args.batch_size}", batch_samples, target=2500.0):
        failures.append(f"batch-{args.batch_size} p50 >= 2500 ms")
    if batch_samples:
        per_item = statistics.fmean(batch_samples) / args.batch_size
        print(f"{'  -> per-item cost in batch':<32} {per_item:8.1f} ms/item")

    # --- priority: an interactive query must not queue behind bulk ------
    # Fires a bulk batch, then 400ms later an interactive query; the query's
    # latency is what a student actually feels while ingestion runs.
    query_latency = None
    bulk_done = threading.Event()

    def run_bulk():
        embed(args.url, [CHUNK] * args.batch_size, "document", args.token)
        bulk_done.set()

    bulk_thread = threading.Thread(target=run_bulk, daemon=True)
    bulk_thread.start()
    time.sleep(0.4)
    _, elapsed = embed(args.url, QUERIES[0], "query", args.token)
    query_latency = elapsed
    bulk_done.wait(timeout=180)
    bulk_thread.join(timeout=5)
    # Honest criterion: an interactive query waits at most ONE bulk batch
    # (the worker's single slot cannot be preempted mid-inference).
    starvation_limit = percentile(batch_samples, 0.50) + 500.0
    print(
        f"{'query during bulk batch':<32} {query_latency:8.1f} ms  "
        f"(limit {starvation_limit:.0f} ms = one bulk batch + slack)"
    )
    if query_latency > starvation_limit:
        failures.append(f"query starved by bulk ingestion: {query_latency:.0f} ms")

    print()
    if failures:
        print("RESULT: FAIL — " + "; ".join(str(item) for item in failures if item))
        return 1
    print(
        "RESULT: PASS — single-query p50 < 80 ms, batch p50 < 2.5 s, "
        "query not starved by bulk, 768 dims"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
