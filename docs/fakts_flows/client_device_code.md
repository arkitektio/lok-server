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
| `deviceName` | Optional name for a newly created device (when the manifest carries `node_id`). |
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
