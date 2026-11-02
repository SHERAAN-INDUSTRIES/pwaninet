# Workspace

The `workspace` app provides an in-vasto entry point for connecting an account to the separate vasto Workspace system. It records whether a user dismissed the prompt or has synced, but does not implement synchronization. The actual sync service belongs to the external Workspace system.

## Routes

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/workspace/sync/` | Show the sync entry page and the account/session values intended for a future handoff. |
| POST | `/workspace/dismiss/` | Record the user's dismissal and return to the home feed. |

The Sync button is intentionally inert. The real REST integration will plug into the button handler in `workspace/templates/workspace/sync.html` and the handoff data prepared by `workspace/views.py`; that integration is to be wired manually by the maintainer. Any future transmission of the session key must use HTTPS and a trusted Workspace origin.
