# Discovery (`/.well-known/fakts`)

> Shared step — the **entry point** every flow starts from, not a flow on its own.

A client knows only the deployment's base URL. It `GET`s `/.well-known/fakts` to
learn the absolute URLs of every fakts endpoint and a few deployment facts, then
drives whichever [flow](./README.md#2-which-flow-when) it needs. This keeps endpoint
paths and the mesh coordinator out of client configuration — the server is the
single source of truth.

- [When to use](#when-to-use)
- [Why](#why)
- [Request](#request)
- [Response fields](#response-fields)
- [Code path](#code-path)

## When to use

Always, first — before starting any flow. A client should treat the URLs it
returns as authoritative rather than hard-coding `/f/...` paths, so a deployment can
relocate endpoints or sit behind a gateway without breaking clients.

## Why

Fakts is designed so a client ships with *only* a base URL. Discovery is what turns
that one URL into a working set of endpoints, and it is where per-deployment facts
(the human `configure` page template, the mesh coordinator URL) are advertised.
Because the server builds these as absolute URLs from the incoming request, the same
image works across dev/staging/prod without reconfiguration.

## Request

`GET /.well-known/fakts` — no body, no auth. Returns a JSON object
(`WellKnownFakts`). There are no sendable params.

## Response fields

| Field | Type | Description |
|---|---|---|
| `name` | str | Deployment name. |
| `version` | str | Fakts protocol version of this deployment. |
| `protocol_version` | str | Protocol version marker (`"2"`). |
| `description` | str? | Deployment description. |
| `base_url` | str | Absolute base of the fakts endpoints (`/f/`). |
| `frontend_url` | str | Deployment base domain. *Deprecated* — prefer `configure`. |
| `configure` | str? | Absolute [client-configure](./client_device_code.md) page template; the literal `{code}` is substituted by the client. |
| `issuer` | str? | OAuth issuer identifier — the `iss` of issued access tokens. |
| `device_authorization_endpoint` | str? | Absolute URL of the [app authorization](./client_device_code.md) endpoint (`/o/app-authorization/`) — RFC 8628 device authorization + dynamic client registration. |
| `token_endpoint` | str? | Absolute URL of the OAuth2 token endpoint — the client polls it with the device-code grant (or exchanges a [redeem token](./redeem_token.md)) and refreshes there. |
| `jwks_uri` | str? | Absolute URL of the JWKS used to verify issued access tokens. |
| `grant_types_supported` | str[] | Grant types the token endpoint accepts, including `urn:ietf:params:oauth:grant-type:device_code` and `urn:fakts:grant-type:redeem`. |
| `token_endpoint_auth_methods_supported` | str[] | Client auth methods; fakts clients are public and use `none`. |
| `mesh_coord_url` | str? | Public ionscale [mesh](./mesh_device_code.md) coordination URL, or `null` when no mesh is configured. |
| `mesh_device_code_start` | str? | Absolute URL of the [mesh start](./mesh_device_code.md) endpoint. |
| `mesh_challenge_url` | str? | Absolute URL of the [mesh challenge](./mesh_device_code.md) endpoint. |
| `mesh_configure` | str? | Absolute [mesh-configure](./mesh_device_code.md) page template; `{code}` is substituted by the machine. |
| `hub_authorization_endpoint` | str? | Absolute URL of the [hub authorization](./hub_device_code.md) endpoint (`/o/hub-authorization/`). |
| `hub_claim` | str? | *Deprecated.* Absolute URL of the hub claim endpoint (`/f/claimhub/`) — only the partner-webhook path still uses it. |
| `hub_configure` | str? | Absolute [hub-configure](./hub_device_code.md) page template; `{code}` is substituted by the client. |

> The same RFC 8414 core (plus `authorization_endpoint` and
> `revocation_endpoint`) is also served at the standard
> `/.well-known/oauth-authorization-server` and, with OIDC extras, at
> `/.well-known/openid-configuration`; it is inlined here so a fakts client
> needs a single discovery request.

> The document advertises the *client*, *mesh*, and *hub* flows explicitly.
> The service-instance flow no longer exists — instances are provisioned
> through the hub flow or the management API.

## Code path

- View: `WellKnownFakts` (`fakts/views.py`), mounted at `.well-known/fakts`
  (`lok_server/urls.py`).
- Model: `WellKnownFakts` (`fakts/base_models.py`).
- `configure` / `mesh_configure` are resolved to absolute URLs by
  `_absolute_configure_url` from `DEPLOYMENT_CONFIGURE_URL` /
  `DEPLOYMENT_MESH_CONFIGURE_URL` (see [CONFIG.md](../../CONFIG.md), `configure_url`
  / `mesh_configure_url`).
