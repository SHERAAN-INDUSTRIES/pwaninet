# Spark retrieval and upload repair

Verified against the local development database on 9 November 2026.

The stored vector corpus was not corrupted. The self-hosted embedding service had stopped, and no document worker was answering. Restored the existing embedding container with restart policy `unless-stopped`, started a scoped document queue worker, and verified a real embedding request and authorized semantic retrieval. Warm query embedding was approximately 62–72 ms; authorized hybrid retrieval was approximately 0.84–0.89 seconds in the measured probes.

Retrieval now has an authorized chunk-text fallback when semantic embedding is unavailable, before legacy metadata search. Only current, active, completed vectors for the configured model enter ordinary semantic candidates. Explicit page lookup can still reference a selected older version. Citations resolve the chunk's actual source file and page.

Upload processing now handles all files in a document version with stable global chunk indices and file provenance. Publishing embedding work waits for database commit; retries are not swallowed. Already extracted versions can resume embedding safely. A failed forced extraction preserves old chunks, and repeated permanently unreadable versions enter reconciliation backoff. Storage keys use unique directories so same-named uploads cannot overwrite each other.

Recovered one available source version into 47 embedded chunks. Final corpus: 2,468 active completed vectors, all 768-dimensional, finite and nonzero. Sixteen eligible versions are now ready. **Sixteen other source objects are missing from storage and one version has no file record**; those require restoring originals or reuploading. A vector repair cannot recreate absent source files.

Timetable extraction previously fabricated a fixed starter schedule after model failure. Removed that fallback and quarantined only the two active schedules matching its exact synthetic IDs/codes. Sources and extracted data remain preserved. Failed/new processing cannot replace a valid active schedule; semester, cohort, institution and academic year boundaries are checked. Actual timetable image extraction remains dependent on configured Gemini vision and was not live-verified.

Verification: the initial targeted PostgreSQL regression run passed 189 tests. Follow-up ingestion/retrieval checks passed 50 tests, and ingestion/reconciliation recovery checks passed 25 tests after subsequent refinements. Tests include actual PDF extraction followed by mocked embeddings and real pgvector retrieval. Live probe evidence is separate from these controlled tests. See [sanitized health snapshot](../verification/spark/rag-health.json).
