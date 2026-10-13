# Client device code — the canonical fakts grant

Registers **one app/client** (a CLI, worker, desktop app) against a user's
organization and hands it everything it needs in a single token response:
an access token (RS256 JWT), a refresh token, and the rendered service
instances.

The shape is RFC 8628 (OAuth device authorization grant) with one addition:
the start request performs **dynamic client registration** — the manifest in
the request mints a *public* OAuth2 client, so no client identity has to exist
in advance.

## 1. App authorization: `POST /o/app-authorization/`

Request body (JSON — `DeviceCodeStartRequest` in `fakts/base_models.py`):

| Field | Type | Default | Description |
|---|---|---|---|
| `manifest` | [`Manifest`](./README.md#manifest) | — (required) | The app describing itself. |
| `expiration_time_seconds` | int | `300` | Requested code lifetime; clamped server-side to 900s. |
| `redirect_uris` | str[] | `[]` | For website-kind clients: registered redirect URIs (enables authorization-code + PKCE). |
| `requested_client_kind` | `development` \| `website` \| `desktop` | `development` | Client kind. |
| `requested_client_role` | `interface` \| `agent` | `interface` | Operational role. |
| `request_public` | bool | `false` | Ask for the client to be marked public. |
| `supported_layers` | str[] | `["web"]` | Layers the client can reach. |

Response:

```json
{
  "status": "granted",
  "device_code": "kJ8f…-43-url-safe-chars-…",
  "user_code": "A7K3…",
  "client_id": "6f0c…",
  "token_endpoint": "https://…/o/token/",
  "verification_uri": "https://…/configure/{code}",
  "verification_uri_complete": "https://…/configure/A7K3…",
  "expires_in": 300,
  "interval": 5
}
```

`device_code` is a full-entropy polling secret; `user_code` is the short
human-transcribable code the configure URL carries (a shoulder-surfed screen
must not leak a polling credential). `client_id` identifies the freshly
registered public OAuth2 client (`token_endpoint_auth_method=none`, no secret);
it is unusable until a human approves the code.

## 2. Approval (human, kontrol)

The device shows the code / opens `verification_uri_complete`. A logged-in user
reviews the manifest and calls `acceptDeviceCode` (management GraphQL):

| Input | Description |
|---|---|
| `deviceCode` | The device code's id (looked up from the code via `deviceCodeByCode`). |
| `hub` | The hub to compose against — **this picks the organization** (`hub.organization`); the caller must be a member. |
| `deviceName` | Optional name for a newly created device (when the manifest carries `device_id`). |
| `declinedRequirements` | Optional requirement keys the user declines (optional requirements only). |

Approval mints the org-scoped fakts `Client` (bound to the approving user's
membership), resolves every manifest requirement to a service instance in the
chosen hub, grants the manifest's scopes (they must exist as org scopes), and
writes the granted scope onto the OAuth2 client. Re-approving an app that
already has a client in the same org **rotates its identity**: the client is
re-pointed at the new registration and the previous OAuth client (and with it
that installation's refresh chain) is deleted.

`declineDeviceCode` requires the `code` itself as proof of possession.

## 3. Poll: `POST /o/token/`

Standard OAuth2 form encoding:

```
grant_type=urn:ietf:params:oauth:grant-type:device_code
device_code=kJ8f…
client_id=6f0c…
```

While pending the endpoint returns HTTP 400 `{"error": "authorization_pending"}`
(or `slow_down` when polling faster than `interval`); a declined code returns
`access_denied`, an expired one `expired_token`. On success:

```json
{
  "access_token": "eyJ…",
  "refresh_token": "…",
  "token_type": "Bearer",
  "expires_in": 3600,
  "scope": "openid profile email read write",

  "client_id": "6f0c…",
  "self": {"deployment_name": "…", "alias": {"host": "…", "ssl": true, "path": "lok", …}},
  "instances": {
    "db": {"service": "com.example.db", "identifier": "3", "aliases": [{…}], "challenge_key": null}
  },
  "statuses": {"db": "granted"}
}
```

The access token is an RS256 JWT (verify against `jwks_uri` from discovery)
carrying `sub` (user id), `org` (organization id), `roles`, `scope`,
`client_id`, `client_app`, `client_release`, `client_device`, `client_role`,
and `aud` (the service identifiers of the granted instances, plus `lok`).

The device code is **single-use** — burned on this response. Do not re-poll.

## 4. Refresh: `POST /o/token/`

```
grant_type=refresh_token
refresh_token=…
client_id=6f0c…
```

No secret. The refresh token **rotates** on every use (the old one is revoked);
each token lives 30 days from issuance, and the whole chain carries an
**absolute cap of 180 days** since the original authorization — an app that
refreshes regularly stays alive for up to six months, then (or after going dark
for a month) a human re-approves. Sessions can also be revoked at any time via
`/o/revoke/` (RFC 7009) or the management API. Every refresh response carries a freshly re-rendered
envelope (`self`, `instances`, `statuses`) — instance aliases are resolved
against the request host, so this is also how configuration changes reach the
client.

## 5. Ongoing authentication

The Bearer JWT is the client's universal credential: services verify it via
JWKS, and lok's own [`/f/report/`](./report.md) telemetry endpoint
authenticates the reporting client from the JWT's `client_id` claim. There is
no other client credential — no client secret, no opaque client token.

## 6. Mesh access (optional)

An app that wants a node on the organization's mesh sends top-level
`"request_auth_key": true` in step 1. If the approver allows it, the step 3
response carries, **once**:

```json
"mesh": {"ionscale_auth_key": "…", "ionscale_coord_url": "https://…"}
```

The key is valid for 15 minutes and registers the app's node (a *sidecar*,
which can reach only the hub the client is bound to). The node itself does not
expire: persist the tailscale state, keyed by `self.sub`, `self.organization`
and `self.hub`, and only ask for a key when not logged in. A refresh response
never carries `mesh`.

**Lost the mesh state** (recreated container, wiped volume)? `POST` to the
discovery document's `mesh_key_endpoint` with `Authorization: Bearer <access
token>` and no body:

```json
{"status": "granted", "mesh": {"ionscale_auth_key": "…", "ionscale_coord_url": "https://…"}}
```

| Status | `reason` | Meaning |
|---|---|---|
| 401 | `not_live` | The session is revoked or expired: re-authorize (step 1). |
| 403 | `not_enrolled` | No mesh access was granted at approval, or the app sent no `device_id`: re-authorize with `request_auth_key`. |
| 409 | `connected` | The sidecar's node counts as online; a key is only for a node that is not. Retry with backoff: a node that went away uncleanly (crash, reboot) can still count as online for a few minutes. |
| 429 | `throttled` | A key was issued less than a minute ago. |

**How long the node lives.** As long as a session of the installation (the same
app, member and device) does. When the session expires (the 30-day / 180-day
limits above) the node loses its access to the hub but is kept for 14 days, so
re-approving the app resumes the same node. When the app is rejected (by the
member who approved it or an organization admin, or by the app itself through
`/o/revoke/` if it is the installation's last session) the node and its key are
deleted at once.
