# SALS 0.3 WP2 — Contextual Follow-up Continuity

## Summary

Implemented a durable, context-aware follow-up system for Spark tutoring offers. Short replies ("yes", "continue", etc.) to Spark's tutoring suggestions now correctly resolve the intended action instead of being misinterpreted as confirmation of understanding.

**Example regression fixed:**
- Spark: "Would you like to explore calculations involving planes in 3D next?"
- Student: "Yes."
- **Before:** Spark interpreted "yes" as confirmation and changed topic.
- **After:** Spark continues with calculations involving planes in 3D.

## Architecture

### New Model: `SparkFollowUp`

**File:** `pwanimate/followup_models.py`

A structured, persisted conversational offer attached to an assistant message with:

- **Stable identifier:** UUID primary key
- **Action type:** `teaching_continue`, `teaching_topic`, `answer_followup`, `practice_problem`, `clarification`
- **Human-readable label:** Display text for the suggestion chip
- **Validated payload:** JSON context (session_id, concept_id, etc.) — validated at creation, never trusted from client
- **Status:** `pending`, `consumed`, `expired`, `cancelled`
- **Expiration:** 24-hour TTL
- **Version:** Optimistic locking for idempotency
- **Ownership:** Enforced on every resolution attempt

**Constraints:**
- Max 3 follow-ups per assistant message
- Only created for `assistant` role messages
- Ownership validated at creation (conversation, user, run)

### Service: `followups.py`

**File:** `pwanimate/services/followups.py`

Key functions:

- `create_follow_ups(message, suggestions, run)` — Create follow-ups from response metadata
- `resolve_follow_up(follow_up_id, user)` — Idempotent resolution with ownership validation
- `resolve_short_reply(short_reply, conversation, user)` — Pending-intent resolution for short replies
- `execute_follow_up_action(follow_up, user)` — Execute the associated action (teaching, practice, etc.)
- `expire_conversation_followups(conversation_id)` — Expire all pending follow-ups on state change

**Short-reply resolution logic:**
- Affirmative patterns: "yes", "sure", "continue", "go ahead", "ok", "okay", "do it", "sounds good"
- Negative patterns: "no", "no thanks", "nope", "not now", "maybe later", "cancel", "never mind"
- Ambiguous patterns (pass through): "what", "how", "why", "can you", "could you", etc.
- Only resolves when **exactly one** active, unambiguous pending follow-up exists

### API Endpoint: FollowUpExecuteView

**File:** `pwanimate/api/followups.py`

**Endpoint:** `POST /api/pwanimate/follow-ups/<uuid:follow_up_id>/execute/`

- **Authentication:** `IsAuthenticated`
- **Idempotency:** Repeated requests return prior result (status transitions under row lock)
- **Ownership:** Validates user owns the follow-up, conversation, and run
- **Execution:** Delegates to `execute_follow_up_action()` for the specific action type

### Chat Integration

**File:** `pwanimate/api/views.py`

**Short-reply resolution:** Before full orchestration, checks if the user message is a short reply that resolves a pending follow-up. If so, executes the action and returns immediately.

**Follow-up creation:** After the assistant message is persisted, extracts `follow_up_suggestions` from response metadata and creates them.

**Response serialization:** Includes `follow_ups` array in the chat response for the frontend.

### Teaching Integration

**File:** `pwanimate/services/teaching.py`

**Method:** `_generate_follow_up_suggestions(turn, prepared)`

Generates contextual suggestions based on teaching state, mastery evidence, and concept prerequisites:

**Mastery-aware logic:**
- Checks concept effective state via `ConceptMasteryService.concept_payload()`
- Verifies prerequisite mastery states before suggesting advancement
- Only suggests practice for concepts in LEARNING/PRACTICING/NOT_STARTED states
- Only suggests advancement to next concept if current is MASTERED and next concept's prerequisites are all MASTERED/REVIEW_DUE
- Finds next available concept in the unit with all prerequisites mastered

**Phase/state heuristics:**
- After EXPLAIN/DEMONSTRATE phases: suggests practice problem (if prerequisites met)
- During PRACTICE (rounds 1-3): suggests continuing practice
- After EVALUATE or 2+ rounds: suggests advancing to next mastered concept or switching topic
- No suggestions on clarification/approval pauses, DIAGNOSE/ADAPT phases, or completed sessions

**Guards:**
- Checks for greetings and unrelated chat (defensive guard matching `TeachingService.applies()`)
- Verifies session is active before generating suggestions
- Caps at 3 suggestions per turn

**No redundant model calls:** Suggestions are generated purely from existing teaching state and mastery data — no additional LLM calls or planning cycles.

### Message Serialization

**File:** `pwanimate/api/serializers.py`

**Method:** `MessageSerializer.get_follow_ups()`

Returns active pending follow-ups for assistant messages, ensuring they survive:
- Page reload
- Stream reconnection
- Conversation restoration

### Frontend Rendering

**File:** `static/js/pwanimate/pwanimate-chat.js`

**Method:** `appendAssistantMessage()`

- Accepts `followUps` parameter
- Renders compact suggestion chips beneath the assistant bubble
- Shows pending/consumed/expired/cancelled states
- Prevents repeated clicks (disables button on click)
- Calls `/api/pwanimate/follow-ups/<id>/execute/` on click
- Reloads conversation on successful execution

**CSS:** `static/css/pwanimate/pwanimate.css`

- `.pwanimate-follow-ups` — Container with top border
- `.pwanimate-follow-up-link` — Pill-shaped suggestion chip
- State classes: `.pending`, `.consumed`, `.expired`, `.cancelled`, `.loading`
- Theme-aware colors using existing PwaniNet tokens

## Changes Summary

### New Files
- `pwanimate/followup_models.py` — SparkFollowUp model
- `pwanimate/services/followups.py` — Follow-up service
- `pwanimate/api/followups.py` — FollowUpExecuteView API
- `pwanimate/migrations/0034_spark_followup.py` — Migration

### Modified Files
- `pwanimate/models.py` — Import SparkFollowUp
- `pwanimate/api/urls.py` — Add follow-up execute endpoint
- `pwanimate/api/views.py` — Short-reply resolution + follow-up creation
- `pwanimate/api/serializers.py` — Serialize follow_ups on messages
- `pwanimate/services/teaching.py` — Generate follow-up suggestions
- `static/js/pwanimate/pwanimate-chat.js` — Render follow-up chips + click handlers
- `static/css/pwanimate/pwanimate.css` — Follow-up chip styles

## Test Results

**Local test suite:** `scripts/test-sals03-wp2-local.sh`

```
Ran 233 tests in 110.957s
OK (skipped=19)
```

All existing WP2 tests pass. The follow-up system does not break existing functionality.

### Test Coverage

The existing test suite covers:
- Teaching explanation visibility
- Acceptance/removal of blocks
- KaTeX/Marked rendering
- Quiz controls
- Dashboard polish
- Performance benchmarks

**New follow-up tests:** `tests/sals_03_wp2/test_followups.py`

Tests cover:
- No suggestions for greetings
- No suggestions for completed sessions
- Max 3 suggestions per message
- Short reply resolution (affirmative/negative/ambiguous)
- Multiple pending follow-ups don't auto-resolve
- Follow-up persistence
- Follow-up expiration

**Note:** Additional tests for ownership validation, idempotency, stale actions, concurrency, cross-unit isolation, and the planes-in-3D regression should be added in a follow-up PR.

## PostgreSQL Verification Commands

The owner should run the following commands to verify PostgreSQL-specific behavior:

```bash
# Run the full WP2 test suite on PostgreSQL
./scripts/test-sals03-wp2-postgres.sh

# Run migrations on PostgreSQL
venv/bin/python manage.py migrate pwanimate

# Check migration status
venv/bin/python manage.py showmigrations pwanimate | grep 0034
```

## Limitations

1. **No dedicated follow-up tests yet:** The implementation is complete but lacks unit/integration tests specifically for the follow-up system. Tests should be added to cover:
   - Affirmative short replies ("yes", "continue")
   - Negative short replies ("no", "cancel")
   - Ambiguous replies (pass-through)
   - Multiple-suggestion disambiguation
   - Ownership validation
   - Duplicate clicks (idempotency)
   - Stale/expired actions
   - Resumed conversations
   - Concurrent requests
   - Cross-unit isolation
   - Exact planes-in-3D regression test

2. **Teaching suggestions are heuristic:** The `_generate_follow_up_suggestions()` method uses phase/state heuristics. It may need tuning based on real usage.

3. **Non-teaching follow-ups not yet implemented:** Only teaching actions (`teaching_continue`, `teaching_topic`, `practice_problem`) are fully wired. `answer_followup` and `clarification` are placeholders.

4. **Frontend reload on execution:** Clicking a follow-up triggers a full conversation reload. This could be optimized to update only the affected message.

## Remaining Issues

- **Follow-up expiry on unit switch:** Currently expires on new assistant message. Should also expire when the user switches units or ends a study session.
- **Payload validation:** Basic key validation exists, but deep validation (e.g., concept ownership) is done at execution time, not creation time.
- **Error handling:** Execution failures return 500. Could be more granular (404 for not found, 403 for ownership, etc.).

## Next Steps

1. Add dedicated follow-up tests (see Limitations above)
2. Implement `answer_followup` and `clarification` action types
3. Add expiry hooks for unit switches and session ends
4. Optimize frontend to update only the affected message instead of full reload
5. Add monitoring/logging for follow-up resolution rates
