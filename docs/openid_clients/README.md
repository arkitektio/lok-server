# OpenID / OAuth2 clients (relying parties)

lok is an **OpenID Connect provider**: other services ("relying parties") log
their users in through lok and receive JWTs that any service can verify. This
module explains how a relying party is registered, the config that must line up
across services, and how to fix the most common error — **"client does not
exist"**.

- [1. How it fits together](#1-how-it-fits-together)
- [2. Registering a relying party](#2-registering-a-relying-party)
- [3. The values that must match across services](#3-the-values-that-must-match-across-services)
- [4. Worked example: ionscale](#4-worked-example-ionscale)
- [5. Per-client claim shaping (`sub` and `email`)](#5-per-client-claim-shaping-sub-and-email)
- [6. Troubleshooting "client does not exist"](#6-troubleshooting-client-does-not-exist)

---

## 1. How it fits together

- lok stores each relying party as an `OAuth2Client`
  (`authapp/models.py`; `fakts.models.OAuth2Client` re-exports the same model).
- Clients are **provisioned on boot** by the `ensureopenid` management command
  (run from `run.sh`) from the `openid_apps` list in the lok config
  (`configuration.py` → `OpenIDAppSettings`, mapped to
  `settings.ENSURED_OPENID_APPS`). Nothing is created by default — if
  `openid_apps` is empty, **no clients exist**.
- lok advertises its endpoints at `{oidc_issuer}/.well-known/openid-configuration`;
  the id/access tokens it issues carry `iss = oidc_issuer` and are verifiable via
  the JWKS at `/lok/o/jwks/`.

## 2. Registering a relying party

Add one entry per relying party to `openid_apps` in the lok config:

```yaml
oidc_issuer: "https://go.arkitekt.live"   # the public issuer RPs discover

openid_apps:
  - client_name: My Service
    client_id: my-service
    client_secret: "<shared secret>"       # 🔒 must equal the RP's configured secret
    redirect_uris:
      - https://my-service.example/oidc/callback
```

On the next boot, `ensureopenid` prints `Created/Updated OpenID client
my-service`. (It also **warns** when `openid_apps` is empty so the gap is visible
at boot rather than at first login.)

## 3. The values that must match across services

A relying party integration only works when these agree on both sides:

| lok config | Relying-party config | Must match |
|---|---|---|
| `oidc_issuer` | the RP's `issuer` | exactly (it's the token `iss` and the discovery base) |
| `openid_apps[].client_id` | the RP's `client_id` | exactly |
| `openid_apps[].client_secret` | the RP's `client_secret` | exactly |
| `openid_apps[].redirect_uris` | the RP's redirect/callback URL | the RP's callback must be listed¹ |
| JWKS at `/lok/o/jwks/` | downstream verifiers' `jwks_uri` | e.g. `authentikate.issuers[].jwks_uri` |

¹ `OAuth2Client.check_redirect_uri` currently returns `True` (a stub), so an
unlisted redirect URI is **not yet rejected** — but list it correctly now so the
config is already right when that check is enforced.

## 4. Worked example: ionscale

`configs/ionscale.yaml` authenticates against lok:

```yaml
auth:
  provider:
    issuer: "https://go.arkitekt.live"
    client_id: "lok-frontend"
    client_secret: "in0929…"
```

For this to work, `configs/lok.yaml` must contain the matching client:

```yaml
oidc_issuer: "https://go.arkitekt.live"
openid_apps:
  - client_name: Lok Frontend
    client_id: lok-frontend
    client_secret: "in0929…"        # identical to ionscale's client_secret
    redirect_uris:
      - https://go.arkitekt.live/auth/callback
      - <ionscale's OIDC callback URL>
```

Flow: ionscale discovers lok at `{issuer}/.well-known/openid-configuration` →
sends the user to the authorize endpoint (`https://go.arkitekt.live/authorize`,
the kontrol SPA consent page) → the user approves → lok redirects back with a
code → ionscale exchanges it at `/lok/o/token/`.

## 5. Per-client claim shaping (`email`)

The `sub` (subject) claim is always the **user** id: the same human is the same
subject across every organization they belong to, and relying parties that need
to keep organizations apart (ionscale does) resolve identities on `(sub, org)`
instead. What *can* be shaped per client, on the `openid_apps` entry, is the
`email` claim; it is optional and defaults to today's behavior.

```yaml
openid_apps:
  - client_name: My Service
    client_id: my-service
    client_secret: "<shared secret>"
    redirect_uris:
      - https://my-service.example/oidc/callback
    email_template: "{username}@corp.example"   # optional
```

**`email_template`** (default unset) — a format string for the `email` claim,
rendered per user from a fixed set of membership variables:

| Variable | Value |
|---|---|
| `{username}` | user's username |
| `{user_id}` | user's id |
| `{email}` | user's own email (empty if none) |
| `{membership_id}` | membership id |
| `{org_slug}` | organization slug |
| `{org_name}` | organization name |

Example: `"{username}@corp.example"`. The template is **validated at boot** — an
unknown variable, attribute/index access (e.g. `{user.email}`), or a template
with no variables fails config load with an actionable message. When unset, the
`email` claim is the user's own email, falling back to a synthetic
`<pk>@users.noreply` address.

Both settings are provisioned by `ensureopenid` and stored on the `OAuth2Client`;
the claims are produced in `authapp/oidc_claims.py` (shared by the id_token path
in `authapp/grants.py` and the userinfo endpoint in `authapp/views.py`, so the
two always agree on `sub` as OIDC requires).

## 6. Troubleshooting "client does not exist"

This means lok has no `OAuth2Client` for the `client_id` the relying party sent.
Almost always one of:

1. **`openid_apps` is missing/empty in the active lok config.** Note that
   `docker-compose.yaml` mounts `configs/lok.yaml` over the default config path,
   so edits must go in the file that is actually mounted. Add the `openid_apps`
   entry and restart; the boot log should show `Created/Updated OpenID client …`.
2. **`client_id` mismatch** between the RP and `openid_apps`.
3. **`client_secret` mismatch** — the client is found but token exchange fails
   with `invalid_client` (a different, later error than "does not exist").
4. **Issuer mismatch** — the RP's `issuer` differs from lok's `oidc_issuer`, so
   discovery points elsewhere.

lok now returns an actionable message naming the missing `client_id` and pointing
at `openid_apps` (see `oauth2_client_by_client_id` and `accept_authorize_code`).
