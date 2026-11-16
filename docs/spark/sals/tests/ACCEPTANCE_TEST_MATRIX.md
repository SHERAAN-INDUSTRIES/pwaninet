# Phase 0/1 acceptance and regression specification

Specification only: no application tests created or executed. Date: 11 November 2026. Inputs: six audit files, five phase0 files and four phase1 files. Expected outcomes below describe approved/proposed behavior, not current test results.

Type codes: U pure unit; S service; A authenticated API; D database integration; C concurrent PostgreSQL transaction; W worker retry/consent; B browser; M migration. Gate codes: P required Phase 1 release; R regression within P; F future capability gate (capability must remain disabled until implemented). Basis E means an inspected existing test covers part of the concern, not that it passes; N new proposed test. For every parameterized row, each case is a separately reported test instance.

## A. Validation

| ID | Type/gate/basis | Preconditions | Input/action | Expected outcome |
|---|---|---|---|---|
| V01 | U/P/N | Strict verdict parser | correct=true; correct=false | Exact corresponding booleans; false never credited |
| V02 | U/P/N | Same | correct='false'; 'true'; 0; 1; null; missing | Reject every case; no certified outcome |
| V03 | U/P/N | Same | Malformed, prose-wrapped, duplicate keys, NaN JSON | Strict parse fails; no salvaged credit |
| V04 | S/P/N | Reserved submission, optional evaluator fake | Invalid verdict/timeout | Inconclusive correct=null, reason, no wrong count or credit |
| V05 | U/P/N | MCQ draft | Blank prompt; missing key; key absent from options; two keys | Draft rejected, no publication |
| V06 | U/P/N | MCQ draft | Duplicate option IDs/normalized labels; duplicate qids | Reject; no ambiguous answer |
| V07 | S/P/N | Requested five questions | Provider returns 0/4/6 usable items; one malformed | Exact count required; no partial scored publication |
| V08 | S/P/N | Valid-shaped but mathematically false key | Verifier recomputes known determinant or reviewed bank mismatch | Quarantine key; schema success cannot certify maths |
| V09 | A/P/N | Owned published validated MCQ | Correct ID / valid wrong ID / unknown ID | True / false /400; unknown ID never invokes LLM |
| V10 | U/P/N | Exact expected 1/2 | '0.5', '1/2', '5e-1', '0.6' | First three correct, last incorrect |
| V11 | U/P/N | Expected 10, abs_tol=.1, rel_tol=0 | 10.1 /10.1001 | Boundary inclusive correct / incorrect |
| V12 | U/P/N | Expected 100, abs_tol=.01, rel_tol=.02 | 102 /102.001 | Correct / incorrect; threshold=max tolerances |
| V13 | U/P/N | Expected zero, abs_tol=.01 | .01 / .0101 | Correct /incorrect without division by zero |
| V14 | U/P/N | Numerical grammar | NaN/Infinity/bool/null/1/0/10**100000/function calls/eval payload | Reject boundedly, no execution or provider fallback |
| V15 | U/P/N | Numerical schema | Negative/nonfinite tolerance, invalid expected, excessive exponent/digits | Reject question, no scored publication |
| V16 | A/P/N | Numerical expected zero | v2 value='0'; legacy numeric JSON 0 | Preserve zero through typed validation; no value-or-empty loss |
| V17 | A/P/N | Certified assessment | Free text/image/voice unsupported type | Explicit unsupported format; no certified correctness |
| V18 | A/P/N | Public assessment response/events/error/export | Inspect nested key-related fields | No answer_key/correct_option_id/private verifier payload disclosed |

## B. Counts and evidence

| ID | Type/gate/basis | Preconditions | Input/action | Expected outcome |
|---|---|---|---|---|
| P01 | S/P/N | Five published items | One correct, four unanswered | Completion20%, accuracy100% (1/1), mastery insufficient_evidence |
| P02 | S/P/N | Five items, no accepted answers | Read scores | Accuracy null; completion0; no mastered state |
| P03 | S/P/N | Five decisive outcomes | Three correct/two wrong | A=G=5,C=3,W=2,U=0; completion100, accuracy60; mastery separately determined |
| P04 | S/P/N | Five accepted | Three correct/one wrong/one inconclusive | Completion100, accuracy75 (3/4), grading completion80, I=1 |
| P05 | S/P/N | Pending evaluation | Read counts then finalize | N=U+P+I+C+W always; accepted pending counts answered, not graded |
| P06 | S/P/E | Checkpoint/summary claims mastery, passive or substantive wrong reasoning | Build projection | No certified evidence from prose; supplement test_study_mode checkpoint tests |
| P07 | S/P/N | Same validated practice and assessed outcome | Recompute | Both visible in own stats; practice excluded from mastery |
| P08 | S/P/N | Goal v1 evidence exists | Replace with v2 then read | Old evidence historical; v2 insufficient evidence; no old percentage inheritance |
| P09 | S/P/N | Legacy no concept/key provenance | Read/import history | legacy_uncertified; no automatic promotion |
| P10 | S/F/N | Reviewed mastery/retention policy installed | Varied independent reasoning evidence then later unseen retention items | Only policy-qualified transition; no immediate repeated-item retained mastery; until policy exists certification disabled |

## C. Transactions and recovery

All C cases use separate PostgreSQL connections and explicit synchronization barriers; no sequential mocks as proof of races.

| ID | Type/gate/basis | Preconditions | Input/action | Expected outcome |
|---|---|---|---|---|
| C01 | C/P/N | Owned open question | Same key/hash simultaneously | One submission/evaluation authority; both return same identity |
| C02 | A/P/N | Key already accepted | Same key/different answer/scope |409 idempotency_conflict; original intact |
| C03 | C/P/N | Two open questions | Concurrent independent answers | Both persist; accurate totals; no JSON overwrite |
| C04 | C/P/N | One open question | Different keys/answers race | First reservation wins; loser409; one scored answer |
| C05 | A/P/N | Response lost after finalize | Retry original key | Canonical persisted result; no new charge/evidence side effect caused by replay |
| C06 | C/P/N | Admission transaction | Kill/abort before commit | No accepted row; same key safely admissible |
| C07 | W/P/N | Admission committed, evaluation uncommitted | Worker loss, lease expires, recovery claims | One final evaluation; pending discoverable without original enqueue |
| C08 | W/P/N | Final commit before acknowledgement | Redeliver same job | Existing result returned, no second evidence/projection event effect |
| C09 | A/P/N | Current content version2 | Submit expected version1 |409 stale version; no answer admitted |
| C10 | C/P/N | v1 submission reserved, evaluator paused | Replace goal v2, resume evaluator | Historical v1 result only; v2 projection unchanged |
| C11 | W/P/N | Evaluation event applied | Duplicate/reorder events | Idempotent projection; no extra points |
| C12 | C/P/N | Old lease worker paused | Reclaim with new token, old returns | Old token cannot finalize |
| C13 | C/P/N | Submission reserved | Academic deletion/feature disable before finalization | Tombstone/policy fence prevents credit/resurrection; history read obeys remaining scope |

## D. Authorization parameter families

D01–D08 are the full parameter families in SECURITY_TEST_PLAN.md, each expanded by role, visibility, access path and lifecycle; they are not one happy-path test per row.

| ID | Type/gate/basis | Preconditions | Input/action | Expected outcome |
|---|---|---|---|---|
| D01 | S,A,D/P/E | Role×visibility×path fixtures | Search/read asset/restore | Uniform approved owner/public/restricted decision, no elevated private bypass |
| D02 | S,A,D/F/N | Sharing implementation enabled; owner explicit grant | Grant/read/revoke/expire, attempt re-share | Exact recipient/actions only; revoked/expired denied; grant tests block sharing release |
| D03 | S,A,D/P/E | Restricted two-institution fixtures | Active/revoked/unrelated enrollment | Only current declared entitlement; no institution fallback |
| D04 | S,D/P/N | Authorized docs across units/categories | Apply unit/category/document filters in all retrieval modes | Intersection before rank/limit; unknown/empty scope empty; no widening |
| D05 | A/P/E | Doc A accessible, file from doc B | Substitute file/version/share IDs | Deny mismatched relationships, no path/bytes/metadata leak |
| D06 | A,D/P/E | Private assets/token/cache fixtures | Replay URL as nonowner; stale URL after revoke | No private delivery; bounded URL expiry unacceptable for immediate proxy-revocation promise |
| D07 | S,A/P/E | Saved collection and restored session, access revoked | Inventory/resume/page read | No stale title/snippet/private bytes or resurrected access |
| D08 | A/P/N | Owned session, foreign assessment/question/user IDs | GET/POST/replay/export/delete | Generic not-found; actor cannot be supplied by model/client |

## E. Memory and retention

| ID | Type/gate/basis | Preconditions | Input/action | Expected outcome |
|---|---|---|---|---|
| E01 | S,A/P/E | New/legacy no versioned consent | Context/recall/welcome | Personal/cross-chat recall off; current owned chat still usable |
| E02 | S,A/F/N | Candidate fact proposed, memory on | Retrieve before approval | No fact or vector content; approval capability gate |
| E03 | S,A/F/N | Exact fact approval | Retrieve approved/reject/revoke/edit | Only approved current version returned; edit needs new approval |
| E04 | W/P/E | Legacy embedding/digest task paused | Disable/forget before success AND failure; retry | No recall or record recreation after cutoff; old tasks cannot bypass |
| E05 | S,A/P/E | Existing enabled=True/digest/vector | Upgrade/read through fallback/welcome | Legacy quarantined, no implied consent or automatic re-index |
| E06 | S,A/F/N | Approved fact store enabled | Owner deletes fact; another user attempts | Owner removes derived retrieval; other user404; no academic deletion |
| E07 | A,D/P/E | Academic session with outcomes | Delete chat through UI AND API | Transcript/digest gone; session/evidence retained, detached APIs/report work |
| E08 | A,D/P/N | Preserved academic evidence | Owner export; foreign ID export; scoped deletion | Owner-only minimal export; foreign404; scoped delete removes evidence and projections |
| E09 | W/P/N | Academic export/delete jobs | Delete/revoke/account switch while queued | No foreign/stale export delivery or late record reconstruction |
| E10 | S,A/P/E | Memory off, current chat and academic evidence | Resume/read current history, request cross-chat recall | Active-context/evidence available by ownership; no cross-chat personal lookup |

## F. Compatibility and migration

| ID | Type/gate/basis | Preconditions | Input/action | Expected outcome |
|---|---|---|---|---|
| K01 | A/P/E | Existing ordinary chat and study session | Reopen with SALS off | Existing transcript/document context works; no assessment side effects |
| K02 | A/P/N | Existing legacy assessment | GET v1/v2 representation | Historical fields preserved/labeled legacy; no certification |
| K03 | A/P/N | Assessment flag off | GET history; POST generate/submit | History allowed; writes explicitly disabled |
| K04 | W,A/P/E | Pending approved action prepared while enabled | Disable before confirm/execute | Server policy recheck refuses new capability action |
| K05 | A/P/E | Current viewer IDs/page/version | Ask about page then restore | Same authorized context; conflict/revocation fails closed |
| K06 | A/P/N | Supported v1 and negotiated v2 clients | Success/error/inconclusive/replay | Stable documented shape; v1 never interprets null as wrong; explicit unsupported response when needed |
| M01 | M/P/N | Disposable DB at verified old leaf with historical rows | Apply proposed additive graph | Stable IDs/transcripts/legacy JSON preserved; no false evidence backfill |
| M02 | M/P/N | Upgraded sessions with outcomes | Both conversation delete endpoints | Academic rows retained; source pointers null without broken history |
| M03 | M/P/N | New v2 evidence | Disable flag, deploy compatibility rollback build | Data readable; no destructive reverse migration or old CASCADE writer |
| M04 | M/P/N | Duplicate key/owner/version fixtures | Apply/check constraints, invalid cross-scope write paths | Uniqueness and declared service/DB owner invariants enforced |
| M05 | M/P/N | Actual resolved release graph/runtime | Empty and historical database upgrade in isolation | No missing dependency/pgvector/runtime error; applied-version evidence recorded |

## G. Browser smoke

All cases run on desktop/mobile, light/dark, authenticated synthetic owner; compare current Django shell without redesign. Existing browser examples under tests/spark guide automation, not proof of these cases.

| ID | Type/gate/basis | Preconditions | Input/action | Expected outcome |
|---|---|---|---|---|
| B01 | B/P/N | One correct/four unanswered; zero graded | Render/reload | Accuracy denominator visible, unknown null shown neutrally; no 100% mastery |
| B02 | B/P/N | Unanswered MCQ/numeric v2 | Select only, submit, simulated400/409/timeout, retry same key | Selection alone no write; clear error; safe pending controls; no duplicate submission |
| B03 | B/P/N | Persisted decisive/inconclusive result | Reload/logout/account switch | Owner sees authoritative result, inconclusive not styled wrong, no other owner's cache |
| B04 | B/R/E | Viewer rail and mobile keyboard | Resize split/open page/collapse rail/theme switch | Usable document/chat layout and page context; no regression to intended split |
| B05 | B/P/N | Quiz UI | Keyboard tab/select/submit, screen-reader status, mobile keyboard/notch | Focus/order/labels visible, controls reachable in safe area, errors announced |

Coverage basis: existing tests inspected in `pwanimate/tests/test_study_mode.py`, `test_study_assistant.py`, `test_document_context.py`, `test_retrieval.py`, `test_clarification_resume.py`, `test_agent_reconciliation.py`; document permissions/signed download tests; no current AssessmentService contract tests located. Assertions of future gates are requirements, never claims that those interfaces exist.
