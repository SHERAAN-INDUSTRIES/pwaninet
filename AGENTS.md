# AGENTS.md — PwaniNet / Vasto

## Purpose
This repository contains the existing PwaniNet application, branded Vasto. Follow these instructions when inspecting, planning, implementing, testing, or reviewing changes. Repository code and current tests are the source of truth; this file is guidance, not proof that any particular component exists.

## Read before editing
1. Inspect the repository layout, README, dependency manifests, configuration, relevant routes, models, templates, static assets, and tests.
2. Trace the real request/data flow for the affected feature. Reuse existing utilities and patterns rather than building duplicate systems.
3. State assumptions and identify unknowns. For architecture-level or cross-cutting work, produce a concise implementation plan before editing.
4. Keep changes narrowly scoped. Do not refactor unrelated apps or rename established public contracts without approval.

## Product boundaries
- Vasto is the existing product and identity provider. Legacy PwaniNet names and URLs may still be valid; do not blindly replace them.
- Spark Adaptive Learning System (SALS) applies only to Spark inside the existing Vasto/PwaniNet application. Do not add Vasto Workspaces or Zane features to SALS.
- Vasto Workspaces and Zane are distinct products/projects; do not mix their architectures into this repository without explicit scope.
- Preserve existing authentication, authorization, private-document ownership, consent, academic progress, and data-deletion behavior.

## Existing frontend and design
- Favor reuse of existing Django templates, HTML, CSS, JavaScript, icons, fonts, and theme variables.
- Preserve the established Vasto layout and support both light and dark modes wherever already implemented.
- Do not rewrite the frontend in React, Vue, or another framework without an explicit architectural decision and migration plan.
- Preserve accessibility, keyboard navigation, responsive behavior, and mobile layouts.
- Never treat screenshots or mockups as proof of current source structure; inspect the actual UI code.

## Backend and data
- Verify the actual Python/Django/DRF versions and dependency lock files before using new APIs.
- Use existing service boundaries, authorization checks, migrations, and transaction patterns.
- Do not bypass access checks in document search, preview, download, or offline caching.
- Do not add database columns or perform destructive migrations casually. Provide migration and rollback notes.
- Never expose secrets, API keys, session tokens, or private documents in logs, static bundles, or generated artifacts.

## Vasto Desktop initiative — proposed architecture, not existing implementation
Goal: installable Linux, Windows, and macOS app with high fidelity to the existing Vasto web UI and an intentionally designed offline-first experience.

### Before choosing a desktop framework
- Audit frontend dependencies, Django template rendering, routing, authentication, media storage, uploads, notifications, and realtime features.
- Prototype the same representative Vasto screens in Tauri 2 and/or Electron. Compare UI fidelity, font rendering, startup time, memory, bundle size, accessibility, and Linux compatibility.
- Do not select a framework based only on marketing claims or bundle size.

### Desktop architecture rules
- Bundle all essential frontend assets locally; never make app startup depend on fetching the production website.
- Keep the existing Django backend as the server of record. Do not bundle the entire production Django server or PostgreSQL database unless separately approved and justified.
- Use a dedicated desktop adapter for platform-specific APIs. Keep domain logic and visual components reusable across web and desktop.
- Treat offline and online states explicitly in the UI. Identify features that remain online-only (for example, live calls and cloud AI inference).
- Use local SQLite or an audited equivalent for structured offline records, plus managed local file storage for downloaded media/documents.
- Define synchronization using stable record IDs, change timestamps or versions, idempotency, retries/backoff, a durable outbox, deletion propagation, and conflict policy.
- Isolate cached records by authenticated account. Encrypt/protect sensitive local data using appropriate platform key storage where practical; purge or lock data on logout/account changes according to the security design.
- Never silently cache private files or content without applying server authorization and user-controlled download/cache policies.
- An offline write is a queued local change, not proof of successful server submission.
- Avoid remote-code execution surfaces and excessive native permissions; restrict IPC commands, filesystem access, external URLs, and navigation.
- Design safe app updates with signing and verifiable release artifacts. Preserve data across updates and provide schema migrations/rollback strategy.

### Release targets
- Linux: evaluate .deb, .rpm and AppImage.
- Windows: evaluate .exe / .msi.
- macOS: evaluate .app / .dmg with signing and notarization.
- Build and test on appropriate target operating systems, ideally through CI; do not promise reliable macOS artifacts from a Linux-only local build.

## Validation expectations
- Run the repository's existing applicable tests and add regression tests for changed behavior.
- For desktop work, test cold launch with network disabled, previously cached content, an account switch, offline writes and reconnect, interrupted sync, local database migration, logout, and app update.
- Verify visual fidelity with screenshots of the same representative page at matched viewport size, zoom, font availability, and theme.
- Report exact commands run, observed results, untested paths, risks, and manual acceptance steps. Never claim tests passed unless executed.

## Agent working style
- Explain relevant decisions concisely, then implement and verify. Prefer incremental, reviewable changes.
- For bug fixes: reproduce, identify root cause, patch, add regression coverage, retest.
- Do not claim a task is complete when blocked by missing credentials, platforms, infrastructure, or unverified browser behavior.
- Ask for approval before irreversible operations, broad data migrations, new production services, or major framework changes.
