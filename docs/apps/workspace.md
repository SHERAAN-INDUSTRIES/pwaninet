# Workspace

The `workspace` app provides an in-Vasto entry point for connecting an account to the separate Vasto Workspace system. Dismissing the prompt is recorded in Vasto; the Workspaces backend owns OIDC identity mapping and its authenticated session.

## Routes

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/workspace/sync/` | Show the connection dialog and link the browser to the Workspaces OIDC authorization-start endpoint. |
| POST | `/workspace/dismiss/` | Record the user's dismissal and return to the home feed. |

The connection dialog starts the Workspaces authorization flow with a top-level browser navigation to `{VASTO_WORKSPACE_BASE_URL}/api/auth/vasto/start/`. Vasto's standard `/o/authorize/` endpoint renders this app's Vasto-branded approval screen instead of OAuth Toolkit's default screen; approving submits the validated OAuth request and redirects to Workspaces' registered callback. Workspaces handles state, nonce, PKCE, identity mapping, and its own session. Vasto user IDs and session keys are not sent to the frontend or across this handoff. Configure `VASTO_WORKSPACE_BASE_URL` as the Workspaces backend origin (for local development, `http://127.0.0.1:8001`).
