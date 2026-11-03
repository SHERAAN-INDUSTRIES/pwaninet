# Vasto ↔ Workspaces route reference (Vasto side)

This document lists the Vasto routes used by Workspaces authentication and the Workspace sync entry point. It does not attempt to catalogue every Vasto product page or API. For browser-facing Workspaces routes and their exact response JSON, see `docs/api/vasto-workspaces-auth-routes.md` in the Vasto Workspaces Backend repository.

## How the services connect

The browser begins authorization at Workspaces. Workspaces redirects the browser to Vasto's OIDC authorization endpoint. Vasto authenticates the person and applies OAuth consent policy. Once approved, Vasto redirects the browser back to Workspaces. Workspaces validates the signed identity, creates its own local user mapping and session, then redirects to the frontend. Vasto does not provide the Workspaces session or Workspaces user ID.

Vasto's OIDC `sub` is the provider identity value Workspaces stores with the issuer. Workspaces uses `(issuer, sub)` to find the linked local Workspaces user. The frontend should not submit either ID to establish a session.

Local development normally uses Vasto at `http://127.0.0.1:8000`, Workspaces at `http://127.0.0.1:8001`, and the frontend at `http://127.0.0.1:3000`. In other environments, use the configured HTTPS origins.

## Workspace sync page routes

### `GET /workspace/sync/`

Requires a signed-in Vasto browser session. Returns the Workspace sync page as HTML. Its Continue action navigates the browser to the configured Workspaces `/api/auth/vasto/start/` URL. This page does not create or expose a Workspaces session.

### `POST /workspace/dismiss/`

Requires a signed-in Vasto browser session and a valid Vasto CSRF token. Saves that the current Vasto user dismissed the sync prompt, then redirects to the Vasto home page.

- **Success:** redirect to `/`.
- **Invalid/missing CSRF token:** Django returns 403.

## Vasto OpenID Connect provider routes

Workspaces reads the discovery document and uses the endpoint URLs it advertises. These are provider-to-client protocol routes; the frontend must not call them directly.

| Method and path (local default) | Caller | Behavior / response |
|---|---|---|
| `GET /o/.well-known/openid-configuration` | Workspaces backend | Returns JSON discovery metadata: issuer and authorization, token, UserInfo, and JWKS endpoint URLs. |
| `GET /o/authorize/` | Browser, following Workspaces redirect | Starts OAuth authorization. Depending on Vasto login and consent state, Vasto renders login/consent pages or redirects to the registered Workspaces callback with an authorization code and state. It is not a JSON profile endpoint. |
| `POST /o/token/` | Workspaces backend only | Exchanges the authorization code using the registered client credentials and PKCE verifier. Returns OAuth token JSON to Workspaces. Never expose this request or its response to frontend code. |
| `GET /o/userinfo/` | Workspaces backend only | Requires the access token. Returns OIDC user claims including `sub`, preferred username, email and verification status, names, and profile-picture URL when available. |
| `GET /o/.well-known/jwks.json` | Workspaces backend | Returns public signing keys used to validate the ID token. The private signing key must never be shared. |

The route paths above are the current local defaults. Workspaces obtains protocol endpoint URLs from discovery, so deployments should keep Vasto's configured issuer and discovery document consistent rather than hard-coding local hostnames.

## Related Workspaces routes

The browser-facing Workspaces API is documented separately:

- `GET /api/auth/vasto/start/` begins authorization and redirects the browser to Vasto.
- `GET /api/auth/vasto/callback/` receives Vasto's redirect; it is not called directly by the frontend.
- `GET /api/auth/me/` returns the local Workspaces user and session expiry metadata as JSON.
- `POST /api/auth/logout/` ends only the Workspaces session and requires Workspaces CSRF protection.

The frontend repository also has a dedicated `docs/authentication-routes.md` reference for the routes it calls and the exact profile response shape.
