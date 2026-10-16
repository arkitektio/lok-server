# Hub device code — whole-hub provisioning on the canonical grant

Stands up a **whole hub** — many service instances and the clients that use
them (+ an optional mesh key) — in one human authorization, and hands the hub
server everything it needs in a single token response: an access token
(RS256 JWT), a refresh token, and the full rendered hub config.

Same shape as the [client grant](./client_device_code.md): the start request
performs dynamic client registration, a human accepts in kontrol, the hub
server polls the standard token endpoint.

## 1. Hub authorization: `POST /o/hub-authorization/`

Request body (JSON — `HubStartRequest` in `fakts/base_models.py`): a
`hub` manifest `{identifier, description?, logo?, instances[], clients[],
request_auth_key}` (instances are [`InstanceRequest`](./README.md#servicemanifest)s
with aliases, clients are `ClientRequest`s `{identifier, manifest, description?,
mandate_key}`, see [§5](#5-the-hubs-own-clients)), plus `expiration_time_seconds`.

Response (RFC 8628 shaped, like app authorization):

```json
{
  "status": "granted",
  "device_code": "kJ8f…-43-url-safe-chars-…",
  "user_code": "A7K3…",
  "client_id": "9c1d…",
  "token_endpoint": "https://…/o/token/",
  "verification_uri": "https://…/hubconfigure/{code}",
  "verification_uri_complete": "https://…/hubconfigure/A7K3…",
  "expires_in": 300,
  "interval": 5
}
```

`device_code` is the full-entropy polling secret, `user_code` the short human
code the hub-configure URL carries, and `client_id` the dynamically registered
public OAuth2 client the hub server will poll as.

## 2. Approval (human, kontrol)

`acceptHubDeviceCode` (management GraphQL) takes `deviceCode`, `organization`
and `allowIonscale`; the caller must be a member. It materializes the whole
manifest atomically inside the organization — the hub, its service instances
(+ roles/scopes/aliases they declare), a mandate for each client request, and (when requested and
allowed) a hub-scoped mesh pre-auth key — and binds the registered OAuth2
client to the hub and to the approving user's membership.

`declineHubDeviceCode` requires the `code` as proof of possession.

## 3. Poll: `POST /o/token/`

Identical to the client grant:

```
grant_type=urn:ietf:params:oauth:grant-type:device_code
device_code=kJ8f…
client_id=9c1d…
```

`authorization_pending` / `slow_down` / `access_denied` / `expired_token` while
unresolved; on success the standard token response **plus the hub envelope**:

```json
{
  "access_token": "eyJ…",
  "refresh_token": "…",
  "token_type": "Bearer",
  "expires_in": 3600,

  "client_id": "9c1d…",
  "self": {"deployment_name": "…", "alias": {…}},
  "auth": {
    "jwks_url": "https://…/.well-known/jwks.json",
    "ionscale_auth_key": null,
    "ionscale_coord_url": "https://mesh.…"
  },
  "instances": {"<instance token>": {"identifier": "…", "private_key": "…"}},
  "clients": {"<client_id>": {"client_id": "…"}},
  "client_requests": {"<request identifier>": {"mandate": "…", "key_sha256": "…", "client_ids": []}}
}
```

The JWT carries `org` (the organization id), a `hub` claim (the hub identifier), and
`aud=["lok"]`. The staged code is single-use; the hub server refreshes with
`grant_type=refresh_token` + `client_id` (no secret, rotating, 30-day sliding /
180-day absolute cap) and the **hub config is re-rendered onto every refresh
response** — new instances and clients propagate within the refresh interval.

Hub servers recognise their clients by the `client_id` claim of each client's
Bearer JWT, verified against `auth.jwks_url`.

## 4. Deprecated: `POST /f/claimhub/` with `{token}`

Only the **KommunityPartner webhook path** still uses the opaque `Hub.token` +
claimhub (auto-configured partner hubs have no device-code approval). The token
is a full-entropy random secret (it was previously *derivable* from the hub
identifier + org slug). Follow-up: move partners onto a key-style grant and
delete `Hub.token` entirely.

## 5. The hub's own clients

A hub can run apps of its own beside its services: a plugin engine, for example, that
starts the apps the organization installs. Approving the hub approves them too. Each
`ClientRequest` brings the **mandate key** the hub already wrote into the app's
environment (`mandate_key`, at least 32 characters; konstruktor generates it), and
approving the hub issues a [mandate](./mandate_key.md) under it (origin `hub`), granted
by the approver or, for a preconfigured partner hub, by the organization's owner. It is:

- **pinned** to the request manifest's `identifier`, `scopes` (a ceiling), `requirements`
  (a ceiling) and `device_id` (when set), but **not to a version**, so the hub's next
  release of the app logs in on the same key, as the same client;
- **without expiry**: it lives as long as the hub and the request do.

lok keeps only the key's SHA-256 and never sends a key back: every hub envelope names
each approved request's key by `key_sha256`, with the `client_ids` that logged in with
it, so the hub can tell which of its keys is approved.

Approving the hub again keeps a mandate whose key is unchanged (re-pinned), supersedes
one whose key changed (the old key and its clients stop working), and revokes the
mandate of a request the manifest no longer makes. Deleting the hub deletes them all.
