# Security test plan

Policy authority: `../phase0/DOCUMENT_ACCESS_POLICY.md`, `MEMORY_AND_RETENTION_POLICY.md`. Existing implementation anchors: document semantic/page/queryset helpers, UnifiedSearchService, saved_document_inventory, DocumentViewSet.get_queryset/download, `documents/views.py:private_resource_asset/serve_download`, `documents/permissions.py`, Spark owned API/run/session helpers. Tests are proposed, not executed.

## D01 actor × resource × path matrix

Instantiate each row across vector, lexical, hybrid, exact-page, inventory, preview, download and restored Spark session. Inspect text/snippets/titles/counts/citations/raw URLs, not merely HTTP status. Staff/superuser/executive tests target ordinary application routes with no implicit admin exception.

| Resource condition | Owner | Unrelated | Staff/superuser/PRESIDENT/DELEGATE nonowner | Shared recipient | Enrolled nonowner | Unenrolled nonowner |
|---|---|---|---|---|---|---|
| Ready private, no grant | Allow declared read action | Deny | Deny | Deny absent active grant | Deny | Deny |
| Ready private, explicit read grant | Allow | Deny | Deny unless named recipient | Allow only granted action (F gate) | Deny unless recipient | Deny unless recipient |
| Ready public | Allow | Allow read | Allow read | Allow read | Allow read | Allow read |
| Ready restricted I1/MAT-A | Allow | Deny | No role bypass | Explicit grant only if released | Allow current I1 scope entitlement | Deny |
| Revoked/expired grant or enrollment | Owner remains | Deny | No bypass | Deny revoked route | Deny revoked route | Deny |
| Archived/deleted/unavailable | No normal content delivery | Deny | Deny | Deny | Deny | Deny |

Owner draft-status inspection is allowed separately; it does not make draft text discoverable through normal retrieval. Public anonymous behavior follows declared endpoint policy. Action-specific grants never imply edit/share. Test staff who also owns a document separately, preventing accidental blanket-deny interpretation.

D02 future sharing lifecycle: owner can issue/revoke exact grant; recipient cannot grant onward, mutate action list, change grantor/owner, access other versions/files or forge expiry. Disable sharing at server while pending and confirm no grant executes. Sharing cannot launch until these cases exist; owner-only access must work while sharing is absent.

D03–D04 restricted/institution and relevance: same unit-like labels under two institutions/private units; membership changes; category/unit intersections on both vector and metadata fallbacks; unknown unit/empty explicit document scope returns empty. Add a high-ranking unauthorized sentinel to verify filtering happens before rank/count/limit. Private unit identity belongs to one user and cannot reference another's custom scope. Cross-institution public discovery is allowed if in requested scope; public relevance is not tenant authorization.

D05 relationship substitution: use accessible document A but file/version from private B in both API download and signed delivery; mismatch share and pk; malformed/conflicting IDs; historical version; foreign attachment. Assert no storage_path, presigned URL or provenance leak. `DocumentViewSet.download` currently selects file_id without a document predicate, so this negative is mandatory.

D06 delivery: private original/preview/thumbnail/cache URLs, range requests and signed token replay by B or elevated nonowner. Bind token to document/file/actor as required; recheck current grants and lifecycle at proxy delivery. Inspect CDN/response caching and redirect destination. Previously downloaded copies cannot be retroactively revoked; tests verify declared online guarantees, account switch cleanup and bounded offline-sync behavior only.

D07 inventories/restoration: revoke share/enrollment after saving in collection, pinning page, creating digest/source citation and preparing background work. Refresh/search/restore must not leak private title/snippet/bytes. No fallback from unavailable page to unauthorized document. Recheck key validity and source scope before creating new assessments from material.

D08 authenticated boundaries: mix session A, assessment B, question C, user IDs in payload; replay another owner's idempotency key/status/job/export URL; change operation namespace; GET/POST/DELETE foreign resources. Generic not-found after authentication; no existence/key/owner disclosure. Session/assessment/evaluation user equality must be enforced in all service/admin/import paths, not just endpoint happy path.

## Memory and retained evidence negatives

E01/E04/E05/E10 verify no personal-memory retrieval when disabled, even through lexical fallback, legacy welcome, queued retry, reset cutoff or current-context code. Same-chat history remains owner-authorized, not blocked by memory opt-out. Proposed fact candidates E02/E03/E06 require owner-approved exact version, rejected/revoked excluded, changing value does not inherit approval. Fact interfaces are F gates until implemented.

E07–E09 cover both chat deletion endpoints, detached evidence, export job token guessing, forged scope and delete while worker pending. Source deletion nulls references rather than copying transcripts into evidence. Export cannot include active answer keys, other owners' sources or personal memories outside requested scope. Academic deletion must block resurrection from JSON mirrors/events/backups restored without tombstones; backup processing guarantees need operations signoff.

No logs/traces/errors/tool activity may contain secret keys, private raw answers, provider credentials or unapproved memory. Use synthetic canaries, allowlist operational metadata and inspect captured logs. Include real session CSRF tests alongside APIClient ownership tests. Existing document elevated-role tests intentionally change expectations under the approved contract; preserve unrelated public reads.

Release severity: any private bypass, cross-user export/delete, leaked answer key, consent bypass or duplicate certified evidence blocks its feature release. No successful exploit or security test pass is asserted here.
