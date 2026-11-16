# Spark runtime dialogue: public execution explanations

## Cause

The current activity projection in `pwanimate/orchestrator/activity.py` rejected `thinking`, `plan`, and `say`, stripped all `detail`/`summary` fields, replaced tool text with fixed labels, and reduced checklist labels to Step 1, Step 2, etc. The browser's `publicEvent` in `static/js/pwanimate/activity-trail.js` independently repeated the stripping. Consequently the existing detail renderer received nothing to display. Provider-private reasoning remains deliberately excluded.

## Change

Keep the private-output boundary and add public explanations derived from known stages, tool identities, and observed outcomes:

- Stage explanations describe request preparation, source review, document context and answer preparation.
- Running tool rows explain the purpose of the check.
- Completed list-returning tools report the actual number of returned results; an empty result is distinguished from failure.
- Pending action proposals explicitly say the action has not executed.
- Failed checks cannot be presented as verified evidence.
- Final review includes the actual successful/failed tool-check counts, and checklists include completed/unresolved step counts.
- Study-block tools now have explicit public labels instead of the unknown-tool fallback.

The backend rebuilds summaries on serialization and journal replay, ignoring incoming raw narrative, tool arguments, source snippets, model updates and provider reasoning. The browser uses the same canonical contract, rebuilds summaries for legacy/hydrated events, and renders summary text using `textContent`. It keeps running-to-completed updates on the same row. No additional model call, database migration, or orchestration path was added.

Checklist labels remain privacy-projected; this change does not publish arbitrary model-generated checklist findings or restore a hidden internal monologue. These are public execution explanations, not a verbatim chain of thought.

## Files

- `pwanimate/orchestrator/activity.py`: canonical explanations and bounded numeric outcome projection.
- `pwanimate/orchestrator/agent.py`: actual tool result counts, pending-action state and final check counts.
- `static/js/pwanimate/activity-trail.js`: matching explanation projection and display for live/history trails.
- `templates/base.html`: activity-script cache version changed to `20261112-explanations`.
- `pwanimate/tests/test_activity_explanations.py`: public-purpose/outcome, safety, actual agent emission, and journal/history replay regressions.
- `tests/spark/activity-explanations.cjs` and `tests/spark/fixtures/activity-explanations.json`: shared backend/browser contract and optional offline browser fixture.
- `tests/spark/activity-privacy.cjs`: expect a canonical public explanation while rejecting arbitrary narration.
- `pwanimate/tests/test_agent_activity.py`: replace a contradictory stale test assertion (both forbidding and reading `agent_trace`) with a gateway-call assertion, preserving the privacy expectation.

Existing approval/question controls, tool execution, automatic collapse behavior, durable waiting/resume and final-answer generation are unchanged by this patch.

## Validation

46 backend tests passed with isolated SQLite settings:

```bash
SPARK_MEMORY_SYNC_SCHEMA=1 DJANGO_SETTINGS_MODULE=tests.spark_memory.settings \
.venv/bin/python manage.py test \
  pwanimate.tests.test_activity_explanations \
  tests.sals_wp3.test_activity_security \
  pwanimate.tests.test_agent_reconciliation \
  pwanimate.tests.test_agent_activity.AgentActivityTests \
  tests.sals_wp3.test_database.InteractionLifecycleDatabaseTests \
  --noinput --verbosity 1
```

JavaScript checks passed:

```bash
node tests/spark/activity-explanations.cjs
node tests/spark/activity-privacy.cjs
node tests/spark/lifecycle-resume.cjs
node tests/spark/stream-collapse.cjs
```

Python compilation, JavaScript syntax, and `git diff --check` passed. The shared fixture is checked against both Python and JavaScript projections. The backend replay test covers persisted journal events and message history.

A real DOM/layout fixture was generated, but Chrome startup failed with `setsockopt: Operation not permitted`. No successful browser rendering or live deployed-app check is claimed. To generate the fixture on a machine where Chrome can start:

```bash
SPARK_ACTIVITY_BROWSER_HTML=/tmp/spark-activity-explanations.html \
node tests/spark/activity-explanations.cjs
```

Load that file in Chrome for the live-row, result replacement, failure, safe-text, width and history-hydration checks. Restart the relevant Django/Celery processes to load backend changes and reload Spark to fetch the versioned script. No database migration is required for this dialogue fix.
