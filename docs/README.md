# vasto Documentation

Use these guides as the maintained documentation set. They describe the current repository and should be updated when behavior changes.

## Audience and publication boundary

Everything in this `docs/` directory is developer or operator documentation and is not intended to be linked from the student-facing site. The only planned student-facing documents are the approved About vasto page, Privacy Policy, and a short third-party licence/attribution notice. Publish those as reviewed site pages; do not expose this documentation index, the site overview, app guides, developer guide, licensing working note, or internal pre-launch checklist to students.

## Product and architecture

- [Site overview](site-overview.md): what the site does, the major user journeys, system components, storage, and the current messaging status.
- [Site owner guide](site-owner-guide.md): a guided explanation of how vasto and Pwanimate fit together, how their main flows work, and what the important libraries do.
- [Developer setup guide](developer-guide.md): clone the repository, configure the local environment, start dependencies, run Django, and work with background services.
- [App guides](apps/README.md): responsibilities, important models, routes, and code entry points for each installed vasto app.
- [Vasto ↔ Workspaces route reference](integrations/vasto-workspaces-route-reference.md): Vasto Workspace sync and OIDC provider routes used by Workspaces, with caller and response behavior.
- [Licensing and attributions working note](licensing-and-attributions.md): current project licence decision and third-party notice inventory steps.

## Pre-launch and public documentation drafts

- [About vasto draft](pre-launch/ABOUT-vasto-DRAFT.md): intended student-facing content after approval.
- [Privacy Policy draft](pre-launch/PRIVACY-POLICY-DRAFT.md): intended student-facing content after review.
- [Short licence notice draft](pre-launch/LICENSE-NOTICE-DRAFT.md): intended student-facing notice after third-party inventory is complete.
- [Internal launch checklist](pre-launch/PRE-LAUNCH-CHECKLIST.md)
- [Internal open-issues list](pre-launch/OPEN-ISSUES.md): unresolved launch findings and required follow-up work.

These documents are working drafts, not live site pages. Publish only the three reviewed public documents; the checklist remains internal.

## How to maintain these docs

Prefer one maintained guide for each topic. Update the relevant guide when code or deployment behavior changes. Keep temporary investigation notes, generated summaries, and one-off implementation reports out of the maintained documentation set. Keep required third-party licence notices with the assets or dependencies they cover.

Historical utility scripts are separated under `scripts/`; older standalone checks and manual pages are under `tests/`. See each folder's README before using them.

The maintained product guides describe the live tree in this repository. `vasto_backup_*` folders and vendored repositories under `pwanimate/repos/` are snapshots/reference material, not the running application.
