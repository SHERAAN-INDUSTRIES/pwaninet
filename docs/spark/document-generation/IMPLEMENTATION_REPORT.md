# Spark chat document generation

Implemented 16 November 2026. This extends Spark's existing tools, agent runs, private document storage and export service. It introduces no database migration, provider, orchestration engine or new academic authorization path.

## Student-facing behavior

An explicit request such as “Create revision notes as PDF and DOCX” now starts an existing durable Spark run. Spark authors the content, persists a verified complete assistant response, renders the requested files, and publishes authenticated download cards in the chat. PDF, DOCX, genuine legacy Word DOC and PPTX are supported. The existing message download menu and collection saver also accept all four formats.

Cards are reconstructed with fresh signed URLs on conversation reload and run replay. A failed conversion shows a format-specific retry action, rather than a fabricated link. Export failures keep the completed answer so a controlled retry does not repeat model generation. The run is completed only when all requested files are ready. A cancelled or partial answer is not published as a completed document task.

“Summarize my Java PDF” alone does not imply an output file request. “Summarize my Java PDF and give me a DOCX” does. Generic document requests default to PDF; presentation requests default to PPTX. The deterministic output-format recognizer is intentionally bounded English parsing, not a universal natural-language guarantee.

## Source-grounded revision material

For a summary/revision/practice-paper export with one selected accessible document or attachment, the existing executor processes all available extracted text in the selected scope, in bounded parts. This avoids presenting a few top-ranked retrieval hits as a whole-book summary. Each complete source part is checkpointed before proceeding. Source permissions, version and chunk manifest are checked again while processing. A changed source requires a fresh request.

Missing, ambiguous, unprocessed or oversized sources ask a focused clarification through the existing owned interaction on the same run. Selected document references can be supplied on that continuation. A page range narrows scope. Defaults bound source input to 1,000,000 characters and 64 model-generated sections. Over-budget requests ask for a chapter/page range before generating summaries.

Coverage explicitly means **available extracted text**, not verified OCR coverage of every physical page. Scanned/unextracted pages are not silently declared summarized. Section/page attribution and real document/attachment identity are retained. Revision questions are labelled practice; this does not implement official examinations, independent mastery evidence, marking policy, or SALS 0.5.

## Rendering and typography

- PDF: existing local Markdown/KaTeX/Prism HTML-to-Chromium pipeline, with a pale `spark.vasto` watermark and dated branded footer. The unwanted default Chromium date header is disabled.
- DOCX: existing Markdown block conversion, editable Word paragraphs/tables, A4 layout, Liberation Serif body, Liberation Sans headings, navy hierarchy and dated running branding. Existing equation-image handling is retained.
- DOC: the completed DOCX is converted by local LibreOffice using a separate temporary profile and a bounded subprocess timeout. Output must have the real Compound Binary signature; renaming a DOCX is never used as a fallback.
- PPTX: editable native slides and tables using python-pptx, bounded content pagination, dated branding, display equations as images with original TeX in slide notes. Inline mathematical notation remains text. This is a clean revision deck renderer, not a diagram/layout design system.

The runtime continues to use its existing export stack. No Typst/Pandoc replacement or new Markdown editor was introduced. Student chat Markdown remains the source body.

The old 180,000-character resource guard limited the **generated answer being exported**, not the source book size or model context window. The new default is 1,000,000, configurable and bounded, with explicit rejection rather than silent clipping. Whole-source processing has its own independent bounds.

## Safety, execution and storage

`generate_document` is registered in the existing tool registry. It accepts only an owned persisted request and authorized assistant source. A planner call before final content exists reports preparation, not file completion. Only the trusted finalizer can export a newly saved complete response before its trace is finalized. Existing saved-source tool use requires persisted completion provenance. Tool observations expose document IDs; server-side finalization produces signed cards.

Heavy rendering happens outside database locks. A short transaction rechecks source content, takes the existing source row lock and rechecks the existing generated-resource identity before writing private files. Refresh updates the original generated Document/DocumentFile instead of creating a duplicate Document. Existing unique identity and private storage are reused.

Generated downloads may be read by their authenticated owner immediately after successful generation, even while optional indexing is pending. This exception is limited to available AI-generated files; pending ordinary uploads and foreign accounts retain the original policy. A copied signed URL does not grant another account access. Responses remain `private, no-store`.

Durable state stores IDs, output formats, completion/errors and source coverage, not signed URLs. A pending-export marker is persisted before conversion so existing interruption/retry recovery can reuse the saved answer. Attempt IDs, cancellation and run ownership fence publication. Optional indexing dispatch failure no longer invalidates an otherwise usable generated file.

Fresh requests execute in the existing request/SSE execution path with durable run/journal persistence; controlled retries continue to use existing recovery/Celery integration. This is not a new worker queue. Cancellation during conversion prevents success publication; conversion itself may finish and leave a private generated resource. No immediate process-kill or orphan-cleanup guarantee is added.

## Changed files

Runtime and storage:

- `pwanimate/services/document_exports.py` — explicit format intent, finalized exports, safe cards, batch reload/error reconstruction.
- `pwanimate/services/document_summary.py` — bounded source processing and section checkpoints in existing execution.
- `pwanimate/services/export_formats.py` — real DOC conversion and PPTX rendering, format MIME definitions and branding.
- `pwanimate/services/generated_documents.py` — renderer styling, four formats, configurable content bound, cache/refresh fixes, render outside transaction.
- `pwanimate/services/agent_execution.py` — source preparation, complete-content persistence, export/retry/publication fences and source selection on resume.
- `pwanimate/services/agent_runs.py` — existing run creation/reuse and request snapshots for document tasks.
- `pwanimate/services/study_session.py` — collection format validation accepts four formats.
- `pwanimate/tools/domain/exports.py` — authorized registered generation tool.
- `pwanimate/tools/registry.py` — registration.
- `pwanimate/orchestrator/agent.py` — trusted request ownership injection and export activity grouping.
- `pwanimate/orchestrator/routing.py` — explicit platform action recognition for file requests.
- `pwanimate/orchestrator/prompts.py` — real file/coverage/practice instructions.
- `pwanimate/orchestrator/service.py` — document answer token policy and completed tool resource references.
- `pwanimate/orchestrator/activity.py` — public generation/section progress vocabulary.
- `documents/views.py` — narrowly scoped generated-owner download readiness exception.

Transport and existing UI:

- `pwanimate/api/views.py` — start/reuse document run and preserve existing approval fences.
- `pwanimate/api/agent_runs.py` — terminal payload reconstruction and authorized continuation source references.
- `pwanimate/api/serializers.py` — batched saved cards and errors.
- `pwanimate/views.py` — server-rendered saved cards and errors.
- `pwanimate/templates/pwanimate/partials/chat_content.html` — four-format menu, saved cards and retry buttons.
- `static/js/pwanimate/pwanimate-chat.js` — safe idempotent cards, resume context references and existing cancel integration.
- `static/js/pwanimate/activity-trail.js` — grouped truthful export/section events.

Verification and documentation:

- `tests/spark_exports/__init__.py`, `tests/spark_exports/urls.py`, `tests/spark_exports/test_generation.py`.
- `tests/spark/generated-documents.cjs`.
- `scripts/test-spark-document-generation-local.sh`.
- This report and `ACCEPTANCE_CHECKLIST.md`.

The pre-existing deleted LibreOffice lock file under `docs/spark/r4/` was preserved and is not part of this implementation.

## Configuration and runtime dependencies

No new paid service is required. Deployments need the existing Python docx/pptx dependencies, Playwright Chromium, local Markdown/math assets and fonts. LibreOffice/soffice is additionally required for real DOC conversion. Missing dependencies fail explicitly with retry/alternative-format feedback. Check both web and worker environments.

Optional Django settings (defaults):

- `SPARK_EXPORT_MAX_CHARACTERS=1000000`, clamped 1,000–4,000,000.
- `SPARK_BOOK_SOURCE_MAX_CHARACTERS=1000000`, clamped 12,000–4,000,000.
- `SPARK_BOOK_SUMMARY_MAX_PARTS=64`, clamped 1–96.
- `SPARK_EXPORT_MAX_SLIDES=250`, clamped 5–500; excess fails rather than discarding content.
- `SPARK_EXPORT_MAX_EQUATIONS=200`, clamped 20–500.
- `SPARK_OFFICE_EXPORT_TIMEOUT=60` seconds, clamped 10–180.
- `SPARK_PDF_EXPORT_TIMEOUT_MS=90000`, clamped 10,000–180,000, used for supported Playwright page operations. It is not a hard end-to-end PDF-print deadline.

Increasing these settings increases resource cost; it does not expand model context windows. A source section uses at most 2,048 requested output tokens; long books require multiple real gateway calls. No paid providers were invoked during this work.

## Verification and owner commands

Executed local results:

- New export suite: **17 tests, 16 passed, 1 native-only skip**, 6.113 seconds. Covers explicit output intent, private four-format persistence, resource identity on replay/refresh, foreign ownership, partial/waiting/cancel rejection, actual chat-to-download integration, failure retry without model calls, source section checkpoints/recovery, source selection on the same run, large-content bounds, verbose slide tables and complete source lists.
- Existing R4/SALS/memory/streaming/activity regression script: **383 tests, 365 passed, 18 skipped**, 122.126 seconds. Existing isolated-settings Axes warnings remain visible. PostgreSQL-only coverage is not implied by this result.
- Native four-format file identity/replay check: **1 passed**, 3.961 seconds; DOC was genuinely converted by LibreOffice (PDF mocked in that particular check).
- Native mathematical PDF/DOCX/DOC/PPTX check: **1 passed**, 28.362 seconds; real Chromium printing, equation images, PPTX TeX notes and genuine LibreOffice DOC conversion. No provider calls.
- Eleven Node contract checks passed: existing activity explanations/privacy, lifecycle resume, Study Mode context, Markdown/math, question controls, composer, stream collapse and follow-up repair, plus the new generated-file card test. Both modified JavaScript files passed `node --check`.
- `git diff --check` passed. Independently rendered synthetic Java revision samples in all four formats were visually inspected through PDF/page images; no clipped content in the checked samples. This is sample QA, not proof of every possible layout.

These are isolated correctness/native-render timings, not production latency measurements or provider benchmarks. The source workflow intentionally uses multiple calls when required for coverage. The measured retry test makes **zero** new gateway calls after a renderer failure; source checkpoint recovery generates only the missing section.

Run from the repository root:

```bash
bash scripts/test-spark-document-generation-local.sh
```

The script uses disposable SQLite with mocked providers and the existing broader R4/SALS regressions. Native conversion can be checked separately in an environment permitting local LibreOffice IPC:

```bash
SPARK_NATIVE_EXPORTS=1 SPARK_MEMORY_SYNC_SCHEMA=1 \
DJANGO_SETTINGS_MODULE=tests.spark_memory.settings .venv/bin/python manage.py test \
  tests.spark_exports.test_generation.GenerationTests.test_all_formats_are_actual_private_files_and_replay_same_identity \
  tests.spark_exports.test_generation.GenerationTests.test_native_math_all_formats --noinput
```

The default unit suite stubs PDF printing and legacy DOC conversion; DOCX/PPTX generation, private persistence, chat integration and real authenticated downloads are exercised. Independent native synthetic QA also rendered all four formats and inspected PDF/page images, including DOC/DOCX/PPTX converted to PDF. A real PDF starts `%PDF`, DOC starts the genuine OLE signature, and DOCX/PPTX are Office ZIP files. Synthetic QA uses temporary files/profiles only. Filesystem-sandbox IPC restrictions required approved native execution outside that sandbox; no owner services or data were touched.

Owner PostgreSQL verification uses the existing disposable Docker infrastructure, not a live database URL:

```bash
.venv/bin/python -m tests.sals_wp0.run --suite sals03wp2 \
  tests.spark_exports tests.spark_r4 tests.sals_wp4 \
  tests.spark_memory tests.sals_academic_continuity
```

This creates and destroys its own PostgreSQL/pgvector container and database. PostgreSQL row-lock/concurrency behavior and full migrated-schema compatibility remain owner verification; SQLite cannot establish those guarantees. See the acceptance checklist for browser, worker, storage and real-source verification. No migrations, deployments, service restarts or live student-data tests were performed.

## Limits and roadmap

Real browser/mobile acceptance, deployment dependencies, actual private object-storage delivery, reconnect/cancel under process death and live-provider content quality remain unverified. Large/wide tables, long mathematical notation and complex diagrams require additional visual QA for representative course materials; PPTX text pagination is bounded and heuristic. This feature does not certify every arbitrary Markdown layout or book summary as publication-ready.

The source workflow currently handles one selected source at a time. Multi-document synthesis remains existing general orchestration, without a whole-source coverage attestation. OCR completeness is not inferred from successful retrieval. No historical memory or academic tracking data was changed.

SRI-01 PostgreSQL regression, Phase B continuity, WP4 browser acceptance, R3 hint restraint, R5 student acceptance, SALS 0.4/0.5, gateway provider expansion and the separate Vasto icon migration remain pending. This implementation does not mark them complete.

The earlier six legacy gateway expectation failures and combined-suite timeouts recorded in the existing R4 reports are not marked resolved by these suites.
