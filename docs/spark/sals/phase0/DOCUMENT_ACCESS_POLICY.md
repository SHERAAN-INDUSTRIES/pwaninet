# Shared document access contract

Approved: private documents are owner-only by default; explicit authorized sharing is required. Applies to private AI-generated and ordinary private documents. Staff, superuser, PRESIDENT and DELEGATE status alone grant no private content access through application endpoints. Direct database administration is outside this endpoint contract; no implicit support bypass is introduced.

## Existing evidence and conflicts

`pwanimate/retrieval/services/document_retrieval.py:build_authorized_chunk_queryset` grants elevated roles access to non-AI documents, otherwise public/owner/programme/direct-enrollment branches. `get_page_chunks` applies version/page constraints. `pwanimate/services/study_session.py:is_user_authorized_for_document` implements a separate policy; `orchestrator/document_context.py:DocumentTurnContext` delegates to it. `search/services/unified_search_service.py:_build_document_queryset` has a different restricted-resource branch. `retrieval/adapters/pwaninet_search.py:search_documents_lexical` does not forward academic_unit/category filters and post-filters document_ids after limit. `services/study_materials.py:saved_document_inventory` checks collection ownership/availability, not current read authorization.

`documents/permissions.py:IsDocumentUploaderOrAdmin` and `documents/api.py:DocumentViewSet.get_queryset` grant elevated access. `documents/views.py:private_resource_asset` already requires the generated resource's owner and private storage. `serve_download` binds signed tokens to the document and requires owner for private files, but restricted resources need uniform current-policy checks. Its GET records downloads, so it was not invoked. `DocumentViewSet.download` looks up explicit file_id independently of the chosen document: constrain file.document_version.document as well as authorizing the document. This is a source-confirmed relationship validation gap; successful unauthorized file delivery was not tested.

`documents/collections/models.py:CollectionShare` exists, but a collection share is not established as a universal document-content grant. Do not silently reinterpret old collection memberships as private file sharing.

## Proposed shared interface

Proposed DocumentAccessPolicy.authorized_documents(actor, action, scope) returns a composable queryset; resolve(actor, reference, action, version, file) returns an authorized resource or generic not-found. These are equivalent policy entry points, not two rule implementations. Pure decision input includes visibility, ownership, live grants, institutional entitlement, lifecycle and version/file relationship. Actions: discover, read_text, preview, download, share, edit, archive. Read grants do not imply sharing/editing. No model-supplied actor IDs.

Rules in order:

1. Validate authenticated actor where required, resource identity and file/version membership; deny deleted/unavailable content. Owner-only management may inspect processing status without exposing ready content.
2. Owner may read ready own resources; draft inspection is a separate owner action. Private content requires ownership or an active explicit recipient grant with the requested action. Elevated roles do not bypass.
3. Public ready resources remain discoverable/readable under existing public policy; download availability may be narrower. Anonymous behavior must be explicit per existing endpoint, not accidentally expanded.
4. Restricted institution resources require explicit institutional scope plus current programme/cohort/unit entitlement, or explicit grant. No fallback to first institution or fuzzy course-name matching as authority. Catalog metadata does not authorize content.
5. Ordinary reads resolve active/latest versions. Pinned historical versions require the same current document authorization plus explicit historical-version availability; old version IDs do not bypass revocation. Embedding readiness is a retrieval condition, not permission.
6. Recheck at retrieval, file delivery, context restoration and background source use. Role/unit/visibility/grant changes invalidate cached allow results. Default to request-scoped policy data, not durable positive access cache.

Authorization occurs before vector similarity, keyword ranking, snippets, counts, pagination and inventories. Then intersect explicit academic-unit/category/document filters; unknown or empty requested scope returns empty results, never an unscoped fallback. Relevance boosts may only reorder authorized in-scope candidates.

## Sharing and revocation

Proposed DocumentAccessGrant, if no existing grant can express the contract, stores document, owner/grantor, explicit recipient, allowed read actions, created/revoked timestamps and optional expiry/version scope. Uniqueness prevents duplicate active grants; creator must be current owner; recipients cannot re-share. Prefer one live grant row per document/recipient with separately recorded grant revisions rather than unnecessary access-framework redesign. Read-time expiry and revocation are mandatory. Public source material cited inside a private document does not make the private copy public.

No grant UI/API implementation is included. A safe first release may disable new sharing entirely and enforce owner-only private access. Full grant activation requires the future sharing gate. Existing share-like records are reviewed, never automatically imported as content grants.

Private objects must not retain publicly reusable URLs. Verify originals/previews/thumbnails and CDN cache behavior; move keys or use authorized delivery when legacy public storage would bypass policy. Signed URLs alone cannot revoke immediately after issue: use authenticated proxy delivery for strict revocation, or explicitly approved bounded expiry. Already downloaded bytes cannot be remotely erased; cached app content must be removed on next authorization sync and account switch, without claiming offline instantaneous revocation.

## Compatibility and acceptance

Privilege removal is an intentional privacy change. Update existing elevated-role test expectations, management workflows and any support paths; do not grandfather undocumented access. Acceptance/security matrix: `../tests/SECURITY_TEST_PLAN.md`. Required proof spans vectors/lexical/hybrid/pages/inventory/viewer/download/restoration, role matrix, file substitution, stale grants, revoked enrollment and category/unit intersection. Runtime storage protection and deployment parity are unresolved release gates, not reasons to retain broad access.
