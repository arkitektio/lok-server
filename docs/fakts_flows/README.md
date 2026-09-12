# Fakts flows

**Fakts** is how a piece of software (a CLI, a worker, a desktop app, a server, a
machine) obtains its configuration and credentials from lok *without* shipping any
secrets in advance. The software knows only the deployment's base URL; everything
else — its OAuth client identity, tokens, service endpoints, mesh keys — is
negotiated at runtime through one of the **fakts flows** documented here.

The client flow is **one canonical, org-scoped OAuth grant**: `/o/app-authorization/`
dynamically registers a *public* OAuth2 client and stages a device code; a human
approves it in kontrol (choosing a hub, and thereby an organization); the device
then polls the standard OAuth2 token endpoint (`/o/token/`) and receives the
**access token, refresh token and rendered service instances in one response**.
Continuity is the rotated refresh-token chain — there are no long-lived opaque
client tokens and no client secrets on devices.

The **hub flow rides the same grant** (`/o/hub-authorization/` → accept →
token endpoint → tokens + hub config in one response). Only the mesh flow (which
mints tailnet pre-auth keys, not OAuth tokens) keeps the JSON choreography under
`/f/`; discovery lives at `/.well-known/fakts` and speaks RFC 8414 vocabulary.
The standard `/o/authorize/` endpoint (PKCE required for public clients) serves
browser-based relying parties, and `/o/revoke/` (RFC 7009) revokes sessions.

- [1. The canonical client grant](#1-the-canonical-client-grant)
- [2. Which flow, when](#2-which-flow-when)
- [3. Response shapes](#3-response-shapes)
- [4. Two param surfaces: machine vs. authorizer](#4-two-param-surfaces)
- [5. Shared request models](#5-shared-request-models)
- [6. Per-flow references](#6-per-flow-references)

---

## 1. The canonical client grant

```
  device                          lok                          human (kontrol)
    │  GET /.well-known/fakts      │                                │
    │─────────────────────────────▶  discovery: endpoint URLs       │
    │                              │                                │
    │  POST /o/app-authorization/  │  dynamic client registration   │
    │    (manifest)                │                                │
    │─────────────────────────────▶  + stage a device code          │
    │◀───────────────────────────── {client_id, device_code,        │
    │                              │  verification_uri_complete,    │
    │                              │  token_endpoint, interval, …}  │
    │                              │                                │
    │     (show the code / open verification_uri_complete)          │
    │                              │  ◀── opens /configure/{code}   │
    │                              │  ── acceptDeviceCode(hub, …)   │
    │                              │      org = hub.organization    │
    │                              │                                │
    │  POST /o/token/              │                                │
    │    grant_type=urn:ietf:params:oauth:grant-type:device_code    │
    │    device_code=… client_id=… │                                │
    │─────────────────────────────▶  400 authorization_pending …    │
    │◀───────────────────────────── 200 {access_token,              │
    │                              │     refresh_token, expires_in, │
    │                              │     scope, client_id,          │
    │                              │     self, instances, statuses} │
    │                              │                                │
    │  POST /o/token/  grant_type=refresh_token (hourly)            │
    │─────────────────────────────▶  new token pair (rotated)       │
    │◀───────────────────────────── + freshly re-rendered instances │
```

Key properties:

- **Dynamically registered public clients.** `/o/app-authorization/` mints an
  `OAuth2Client` with no secret (`token_endpoint_auth_method=none`). The
  client's ongoing identity is its `client_id` plus the rotated refresh chain;
  losing the chain means a human re-approves.
- **Org-scoped by construction.** Approval binds the client to the approving
  user's membership in the chosen hub's organization; every issued token's
  subject *is* that membership, and the JWT carries `org` (the organization id).
- **One response.** Tokens and the rendered instances arrive together; every
  refresh re-renders the instances (aliases are host-aware), so config drift
  propagates without re-approval.
- **Re-approval rotates identity.** Accepting the same app again re-points the
  fakts client at the new registration and deletes the old OAuth client,
  severing the previous installation's refresh chain.
- **Ongoing authentication is the JWT.** `/f/report/` (and services, via JWKS)
  authenticate the client by its Bearer access token's `client_id` claim.

## 2. Which flow, when

| Flow | Use it to… | Human authorizes? | Credential you get back |
|---|---|---|---|
| [Client device code](./client_device_code.md) | register **one app/client** (a CLI, worker, desktop app) against a user's org | ✅ yes | access + refresh tokens + instances, in one token response |
| [Hub device code](./hub_device_code.md) | stand up a **whole hub** — many instances + clients (+ optional mesh key) in one authorization | ✅ yes | access + refresh tokens + the full hub config, in one token response |
| [Mesh device code](./mesh_device_code.md) | let a **machine join the org's mesh** (tailnet) with a configurable machine name | ✅ yes | a single-use ionscale **pre-auth key** + coord URL + machine name |
| [Redeem token](./redeem_token.md) | provision a client **non-interactively** from a pre-shared one-time token (CI, headless installs) | ❌ no | same combined token response, via `urn:fakts:grant-type:redeem` |

Removed flows: **retrieve** (unauthenticated public-app token handout) is gone —
website-kind apps use the standard authorization-code + PKCE flow via
`/o/authorize/` and receive their instances in the token response; **claim**,
the client/hub **challenge** polls, and the whole **service-instance flow**
(unused) are gone — subsumed by the token endpoint or deleted. `/f/claimhub/`
survives only (deprecated) for the partner-webhook path.

Shared steps: [discovery](./discovery.md) is the entry point every flow starts
from; [report](./report.md) is client-health telemetry (Bearer-authenticated).

## 3. Response shapes

**The authorization endpoints** (`/o/app-authorization/`, `/o/hub-authorization/`)
and the **`/f/` endpoints** (mesh flow, report) return a JSON object whose
`status` field is the protocol signal (`granted` / `pending` / `denied` /
`expired` / `error` / `reported`), always with HTTP 200 — clients branch on the
body. Anonymous endpoints are rate limited (HTTP 429 `slow_down`).

**The token endpoint** (`/o/token/`) speaks standard OAuth2: HTTP 200 with a
token response on success, HTTP 400 with `{"error": …}` otherwise. While the
human has not decided yet the poll returns `error=authorization_pending`
(`slow_down` when polling faster than `interval`); a declined code returns
`access_denied`; an expired one `expired_token`. On success the response carries
the standard members (`access_token` RS256 JWT, `refresh_token`, `token_type`,
`expires_in`, `scope`) **plus** the fakts envelope:

| Member | Description |
|---|---|
| `client_id` | The public OAuth2 client id (also needed for refresh). |
| `self` | `{deployment_name, alias}` — how to reach this lok deployment. |
| `instances` | `{requirement_key → {service, identifier, aliases[], challenge_key?}}` — the rendered service instances. |
| `statuses` | `{requirement_key → "granted" \| "denied" \| "unavailable"}`. |

`device_code` is a **full-entropy secret** distinct from the short human
`user_code` (which is what the configure URL carries), and it is **single-use**:
burned on the first successful token response. Refresh with
`grant_type=refresh_token`, `refresh_token`, `client_id` (no secret); the
refresh token rotates on every use, carries an absolute chain cap (180 days
since the original authorization on top of the 30-day sliding window), and the
envelope is re-rendered onto every refresh response. Sessions can be revoked at
`/o/revoke/` (RFC 7009) or org-wide via the management API.

## 4. Two param surfaces

For the interactive flows, "sendable params" live on **two different wires**, and
each flow page documents both:

- **What the machine sends** — snake_case JSON to the REST endpoints under `/f/`
  (`requested_client_kind`, `expiration_time_seconds`, `requested_machine_name`, …) —
  the app authorization endpoint lives at `/o/app-authorization/` — and
  form-encoded OAuth2 params to `/o/token/`.
  The REST bodies are the pydantic request models in `fakts/base_models.py`.
- **What the authorizer sends** — camelCase GraphQL inputs to the management schema
  (`acceptXDeviceCode` / `declineXDeviceCode`), issued by the logged-in user in
  kontrol. This is where the **organization** (via the hub), and choices like the
  final machine name or `allowIonscale`, are actually set. These are the
  `@kante.input` classes in `api/management/mutations/`. Declining requires the
  `code` itself as proof of possession.

## 5. Shared request models

These nested models recur across flows. They are defined once here; the flow pages
link back rather than repeat them.

### `Manifest`
Describes a client/app. Used by [client device code](./client_device_code.md),
[redeem](./redeem_token.md), and inside a hub's `clients`.

| Field | Type | Default | Description |
|---|---|---|---|
| `identifier` | str | — (required) | Unique app identifier (reverse-domain, e.g. `com.example.app`). Org-scoped: the same identifier in two organizations is two registrations. |
| `version` | str | — (required) | App version. |
| `title` | str? | `null` | Human display name for the App/Release. |
| `description` | str? | `null` | Human description. |
| `logo` | str? | `null` | URL to a logo; downloaded and validated at start. |
| `scopes` | str[] | `[]` | Requested scopes (must exist as org scopes; granted scopes land in the token's `scope`). |
| `requirements` | [`Requirement`](#requirement)[] | `[]` | Services the client needs to run. |
| `node_id` | str? | `null` | Stable id of the node the client runs on (creates/links a `Device`). |
| `authors` | str[] | `[]` | Maintainers. |
| `keywords` | str[] | `[]` | Discovery tags. |
| `license` | str? | `null` | SPDX id or free text. |
| `homepage` | str? | `null` | Homepage URL. |
| `repo_url` | str? | `null` | Issue-tracker / repo URL. |
| `public_sources` | [`PublicSource`](#publicsource)[]? | `null` | Where the client can be found. |

#### `Requirement`
| Field | Type | Default | Description |
|---|---|---|---|
| `key` | str | — (required) | Requirement key the config fills in. |
| `service` | str | — (required) | Reverse-domain service identifier that satisfies it. |
| `optional` | bool | `false` | If true, the client runs even when unmet (user may decline it). |
| `description` | str? | `null` | Shown to the user when asked to grant it. |

#### `PublicSource`
| Field | Type | Default | Description |
|---|---|---|---|
| `kind` | `"github"` \| `"website"` | — (required) | Source kind. |
| `url` | str | — (required) | Source URL. |

### `ServiceManifest`
Describes a service instance. Used inside a hub's `instances`.

| Field | Type | Default | Description |
|---|---|---|---|
| `identifier` | str | — (required) | Reverse-domain service identifier. |
| `version` | str | — (required) | Service version. |
| `description` | str? | `null` | Human description. |
| `logo` | str? | `null` | Logo URL. |
| `roles` | `{key, description?}`[] | `[]` | Roles this service defines. |
| `scopes` | `{key, description?}`[] | `[]` | Scopes this service defines. |
| `node_id` | str? | `null` | Stable node id (creates/links a `Device`). |
| `instance_id` | str? | `"default"` | Distinguishes multiple instances of the same service. |
| `public_sources` | [`PublicSource`](#publicsource)[]? | `null` | Where the service can be found. |
| `challenge_key` | str? | `null` | Base64 raw Ed25519 public key (32 bytes) for verifying signed alias challenges. |

### `StagingAlias`
An advertised endpoint (URL) for a service instance. Used in the `staging_aliases`
of the service flow and the `aliases` of a hub instance.

| Field | Type | Default | Description |
|---|---|---|---|
| `id` | str | — (required) | Unique alias id. |
| `name` | str? | `null` | Human label. |
| `ssl` | bool | `true` | Whether the alias is served over TLS. |
| `host` | str | — (required) | Host. |
| `port` | int? | `null` | Port. |
| `path` | str? | `null` | Path. |
| `challenge` | str? | `null` | Health/verify URL (200 ⇒ reachable). |
| `kind` | str | `"absolute"` | `absolute` or `relative` (resolved against the linking request). |
| `scope` | `"local"` \| `"network"` \| `"public"` \| `"ionscale"` | `"local"` | Reachability scope. |
| `public` | bool | `false` | If publicly reachable, the coordinator can health-check it directly. |

## 6. Per-flow references

- [Client device code (the canonical grant)](./client_device_code.md)
- [Hub device code](./hub_device_code.md)
- [Mesh device code](./mesh_device_code.md)
- [Redeem token](./redeem_token.md)
- [Discovery (`/.well-known/fakts`)](./discovery.md)
- [Report](./report.md)

> Out of scope: `/.well-known/fakts-challenge` (`lok_server/urls.py`) is a
> placeholder view with no implemented protocol, and the remaining `/lok/o/*`
> OIDC endpoints are the standard OpenID flow — see
> [openid_clients](../openid_clients/README.md), not here.
