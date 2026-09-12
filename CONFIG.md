# Lok — Configuration Reference

This document explains how the **lok** service is configured, then lists every
configuration value, its environment-variable name, its default, and what it does.

Lok is the central authentication / identity provider: it issues OIDC/OAuth2 tokens,
provisions users, organizations and clients, and publishes the verifying keys other
Arkitekt services trust. Its configuration is therefore the richest in the deployment.

The single source of truth for the schema is
[`lok_server/configuration.py`](lok_server/configuration.py); this file documents it for
humans. If the two ever disagree, the code wins — and you can always print the live,
resolved configuration with `python manage.py validate_settings` (see below).

---

## How configuration works

Configuration is a typed [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
schema. Values are resolved from several sources, **highest precedence first**:

1. **Init kwargs** — values passed directly in code (rarely used; tests).
2. **Environment variables** — override anything in the YAML file.
3. **The YAML file** — [`config.yaml`](config.yaml) by default.
4. **File secrets** — Docker/systemd secret files, if used.

So an environment variable always beats the YAML file, which makes containerized
overrides easy without editing the mounted config.

### The YAML file

By default the service reads `config.yaml` next to the project. Point it elsewhere with
the `ARKITEKT_CONFIG_FILE` environment variable:

```bash
ARKITEKT_CONFIG_FILE=/etc/lok/config.yaml python manage.py runserver
```

The file is a nested mapping, one top-level key per configuration *block*:

```yaml
django:
  secret_key: "change-me"
  debug: false
postgres:
  db_name: lok
  username: lok
  password: "change-me"
  host: db
  port: 5432
redis:
  host: redis
  port: 6379
```

### Environment variables (the `__` rule)

Every value is also settable from the environment. The nesting is expressed with a
**double-underscore** (`__`) delimiter, and names are case-insensitive:

| YAML path | Environment variable |
|---|---|
| `postgres.password` | `POSTGRES__PASSWORD` |
| `postgres.port` | `POSTGRES__PORT` |
| `django.debug` | `DJANGO__DEBUG` |
| `redis.channel_prefix` | `REDIS__CHANNEL_PREFIX` |
| `private_key` (top-level) | `PRIVATE_KEY` |
| `oidc_issuer` (top-level) | `OIDC_ISSUER` |

Lists and nested objects (e.g. `authentikate.issuers`, `openid_apps`, `users`,
`organizations`) are awkward to express as environment variables — prefer the YAML file
for those and use env vars for the flat scalars (hosts, ports, passwords, toggles).

### Secrets fail fast

Fields marked **secret / required** below have **no default**. If they are missing from
both the YAML file and the environment, the service refuses to start and raises a
`pydantic.ValidationError` naming the missing field. The same error blocks
`manage.py` entirely, so a broken config cannot be deployed silently.

### Validating a configuration

Run the bundled command to load the config exactly as the app would, validate it, and
print the fully-resolved result as a tree with **secrets redacted**:

```bash
python manage.py validate_settings
```

- Valid config → prints a green `Configuration valid` tree and exits `0`.
- Invalid config → prints each offending field and its error, and exits `1`.

It honors `ARKITEKT_CONFIG_FILE`, so you can validate an alternate file the same way.
(Note: because Django loads settings on startup, a fundamentally invalid config also
surfaces the same validation errors when running *any* `manage.py` command.)

---

## Configuration reference

Secret fields are flagged with 🔒. "Required" means there is no default.

### `django` — core Django framework settings

| Key | Env var | Type | Default | Description |
|---|---|---|---|---|
| `secret_key` 🔒 | `DJANGO__SECRET_KEY` | str | **required** | Django `SECRET_KEY` for cryptographic signing. |
| `debug` | `DJANGO__DEBUG` | bool | `false` | Enable Django debug mode. Never enable in production. |
| `hosts` | `DJANGO__HOSTS` | list[str] | `["*"]` | `ALLOWED_HOSTS` entries. |
| `use_x_forwarded_host` | `DJANGO__USE_X_FORWARDED_HOST` | bool | `true` | Trust the `X-Forwarded-Host` header behind a reverse proxy. |
| `secure_proxy_ssl_header` | `DJANGO__SECURE_PROXY_SSL_HEADER` | bool | `true` | Trust `X-Forwarded-Proto` to detect HTTPS behind a reverse proxy (`SECURE_PROXY_SSL_HEADER`). Disable when not behind a TLS-terminating proxy. |
| `allow_insecure_transport` | `DJANGO__ALLOW_INSECURE_TRANSPORT` | bool | `false` | Let OAuth2/OIDC endpoints accept plain-HTTP requests (sets authlib's `AUTHLIB_INSECURE_TRANSPORT`). Enable when lok deliberately runs without TLS, or behind a proxy that doesn't forward `X-Forwarded-Proto`. |
| `cors_allowed_origins` | `DJANGO__CORS_ALLOWED_ORIGINS` | list[str] | `[]` | Browser origins (`scheme://host[:port]`) allowed to call the OAuth/OIDC (`/o/`), fakts (`/f/`) and discovery (`/.well-known/`) endpoints cross-origin (`CORS_ALLOWED_ORIGINS`). Empty means no cross-origin browser access unless `cors_allow_all_origins` is set. Session-authenticated surfaces (management GraphQL, allauth) never get CORS headers. |
| `cors_allow_all_origins` | `DJANGO__CORS_ALLOW_ALL_ORIGINS` | bool | `false` | Answer every `Origin` on those endpoints (`CORS_ALLOW_ALL_ORIGINS`). They are public and bearer-authenticated (no cookies cross-origin), so this is safe when web apps live on arbitrary hosts; prefer an explicit `cors_allowed_origins` list when the hosts are known. |
| `admin` | `DJANGO__ADMIN__*` | object | **required** | Superuser provisioned on first boot (see below). |
| `csrf_trusted_origins` | `DJANGO__CSRF_TRUSTED_ORIGINS` | list[str] | `["http://localhost", "https://localhost"]` | `CSRF_TRUSTED_ORIGINS` for unsafe (POST) requests. |
| `force_script_name` | `DJANGO__FORCE_SCRIPT_NAME` | str | `""` | URL path prefix this service is served under (`FORCE_SCRIPT_NAME`). |
| `language_code` | `DJANGO__LANGUAGE_CODE` | str | `en-us` | Django `LANGUAGE_CODE`. |
| `time_zone` | `DJANGO__TIME_ZONE` | str | `UTC` | Django `TIME_ZONE`. |
| `log_level` | `DJANGO__LOG_LEVEL` | str | `INFO` | Root logger level (e.g. `DEBUG`, `INFO`, `WARNING`). |

#### `django.admin` — superuser created on first boot

| Key | Env var | Type | Default | Description |
|---|---|---|---|---|
| `username` | `DJANGO__ADMIN__USERNAME` | str | **required** | Superuser login name. |
| `password` 🔒 | `DJANGO__ADMIN__PASSWORD` | str | **required** | Superuser password. |
| `email` | `DJANGO__ADMIN__EMAIL` | str | `null` | Superuser email address. |

### `postgres` — PostgreSQL database (Django `DATABASES['default']`)

| Key | Env var | Type | Default | Description |
|---|---|---|---|---|
| `engine` | `POSTGRES__ENGINE` | str | `django.db.backends.postgresql` | Django database backend. |
| `db_name` | `POSTGRES__DB_NAME` | str | **required** | Database name. |
| `username` | `POSTGRES__USERNAME` | str | **required** | Database user. |
| `password` 🔒 | `POSTGRES__PASSWORD` | str | **required** | Database password. |
| `host` | `POSTGRES__HOST` | str | **required** | Database host. |
| `port` | `POSTGRES__PORT` | int | `5432` | Database port. |

### `redis` — Redis connection (channel layer / cache)

| Key | Env var | Type | Default | Description |
|---|---|---|---|---|
| `host` | `REDIS__HOST` | str | **required** | Redis host. |
| `port` | `REDIS__PORT` | int | `6379` | Redis port. |
| `channel_prefix` | `REDIS__CHANNEL_PREFIX` | str | `lok` | Key prefix for the `channels_redis` channel layer. |

### `authentikate` — inbound token verification

Configures how incoming JWT access tokens are verified (the shared `authentikate`
library). Even though lok *issues* tokens, it must also *verify* the tokens presented
to its own API, so it lists itself as a trusted issuer. At least one issuer is required.

| Key | Env var | Type | Default | Description |
|---|---|---|---|---|
| `issuers` | — (use YAML) | list[issuer] | **required** | Trusted token issuers whose keys verify incoming tokens (see issuer kinds below). |
| `authorization_headers` | `AUTHENTIKATE__AUTHORIZATION_HEADERS` | list[str] | `["Authorization", "X-Authorization", "AUTHORIZATION", "authorization"]` | Request headers searched (in order) for a Bearer token. |
| `static_tokens` | — (use YAML) | map | `{}` | Pre-defined tokens that bypass signature verification. **Tests only.** |

Each entry in `issuers` is discriminated by its `kind`:

- `kind: rsa` — inline PEM/SSH RSA public key. Fields: `iss`, `kid` (alias of `key_id`, default `1`), `public_key`.
- `kind: rsa_file` — RSA public key read from a PEM file. Fields: `iss`, `kid`, `public_key_pem_file`.
- `kind: jwks_dict` — inline JWKS document. Fields: `iss`, `jwks` (a dict with a `keys` list).
- `kind: jwks_uri` — JWKS fetched from a remote endpoint. Fields: `iss`, `jwks_uri`.

```yaml
authentikate:
  issuers:
    - kind: rsa
      iss: lok
      kid: lok-key-1
      public_key: "ssh-rsa AAAA..."
  static_tokens: {}
```

### `lok` — issuer key material exposed by this service

Lok's own identity-provider key material (separate from the OIDC signing `private_key`
top-level field). Optional; defaults to an empty block.

| Key | Env var | Type | Default | Description |
|---|---|---|---|---|
| `public_key` | `LOK__PUBLIC_KEY` | str (SSH/PEM) | `null` | Lok public key used to verify issued tokens. |
| `static_tokens` | — (use YAML) | map | `{}` | Pre-shared static tokens. **Tests only.** |

### `datalayer` — S3 storage and buckets

S3 connection and bucket bindings for the datalayer module (replaces the old top-level
`s3` block). `access_key`, `secret_key` and the `media` bucket are required.

| Key | Env var | Type | Default | Description |
|---|---|---|---|---|
| `access_key` 🔒 | `DATALAYER__ACCESS_KEY` | str | **required** | S3 access key. |
| `secret_key` 🔒 | `DATALAYER__SECRET_KEY` | str | **required** | S3 secret key. |
| `host` | `DATALAYER__HOST` | str | `null` | S3 endpoint host. |
| `port` | `DATALAYER__PORT` | int | `null` | S3 endpoint port. |
| `protocol` | `DATALAYER__PROTOCOL` | str | `http` | S3 endpoint protocol (`http` or `https`). |
| `region` | `DATALAYER__REGION` | str | `us-east-1` | S3 region name. |
| `default_acl` | `DATALAYER__DEFAULT_ACL` | str | `private` | Default ACL applied to stored objects (`AWS_DEFAULT_ACL`). |
| `querystring_expire` | `DATALAYER__QUERYSTRING_EXPIRE` | int | `3600` | Presigned URL lifetime in seconds (`AWS_QUERYSTRING_EXPIRE`). |
| `file_overwrite` | `DATALAYER__FILE_OVERWRITE` | bool | `false` | Overwrite existing files on name collision (`AWS_S3_FILE_OVERWRITE`). |
| `secure` | `DATALAYER__SECURE` | bool | `null` | Use TLS for S3. When `null`, derived from `protocol == 'https'`. |
| `media` | — (use YAML) | object | **required** | Bucket for media / general file storage. Each bucket binding is `{ bucket: <name> }`. |
| `zarr` | — (use YAML) | object | `null` | Bucket for Zarr arrays. |
| `parquet` | — (use YAML) | object | `null` | Bucket for Parquet tables. |
| `bigfile` | — (use YAML) | object | `null` | Bucket for large binary files. |

### `account` — django-allauth account / MFA behavior

Controls login, email verification and multi-factor behavior of the bundled
django-allauth flows. All optional with sensible defaults.

| Key | Env var | Type | Default | Description |
|---|---|---|---|---|
| `email_verification` | `ACCOUNT__EMAIL_VERIFICATION` | str | `none` | `ACCOUNT_EMAIL_VERIFICATION` (`none` / `optional` / `mandatory`). Governs **local** email/password accounts. |
| `social_email_verification` | `ACCOUNT__SOCIAL_EMAIL_VERIFICATION` | str \| null | `null` | `SOCIALACCOUNT_EMAIL_VERIFICATION` — verification policy for **social/OIDC** logins, independent of `email_verification`. `null` inherits `email_verification`; set `none` to admit IdP-authenticated users without our own confirmation (see below). |
| `social_email_required` | `ACCOUNT__SOCIAL_EMAIL_REQUIRED` | bool \| null | `null` | `SOCIALACCOUNT_EMAIL_REQUIRED` — whether an email must be present to sign up via a social provider. `null` inherits allauth's derivation; set `false` to admit providers that release no email (e.g. ORCID). |
| `login_methods` | `ACCOUNT__LOGIN_METHODS` | list[str] | `["username"]` | `ACCOUNT_LOGIN_METHODS` — identifier(s) users log in with (`username` / `email` / `phone`). Set to `["email"]` to enable login via email. |
| `signup_fields` | `ACCOUNT__SIGNUP_FIELDS` | list[str] | derived | `ACCOUNT_SIGNUP_FIELDS`, e.g. `["email*", "password1*", "password2*"]` (`*` = required). When omitted, derived from `login_methods`. |
| `login_by_code_enabled` | `ACCOUNT__LOGIN_BY_CODE_ENABLED` | bool | `true` | Enable login by emailed code (`ACCOUNT_LOGIN_BY_CODE_ENABLED`). |
| `mfa_trust_enabled` | `ACCOUNT__MFA_TRUST_ENABLED` | bool | `true` | Allow trusted devices (`MFA_TRUST_ENABLED`). |
| `mfa_webauthn_enabled` | `ACCOUNT__MFA_WEBAUTHN_ENABLED` | bool | `true` | Offer WebAuthn security keys/passkeys as an MFA type (adds `webauthn` to `MFA_SUPPORTED_TYPES`). Browsers require a secure *page* origin, judged in the browser — so this is not gated on `django.allow_insecure_transport`, which is normal behind a TLS-terminating gateway. |
| `mfa_passkey_login_enabled` | `ACCOUNT__MFA_PASSKEY_LOGIN_ENABLED` | bool | `true` | `MFA_PASSKEY_LOGIN_ENABLED` — let a passkey replace the password at login. Requires `mfa_webauthn_enabled`. |
| `mfa_passkey_signup_enabled` | `ACCOUNT__MFA_PASSKEY_SIGNUP_ENABLED` | bool | `false` | `MFA_PASSKEY_SIGNUP_ENABLED` — passwordless signup. Requires `email_verification_by_code_enabled` (allauth refuses to start otherwise). |
| `email_verification_by_code_enabled` | `ACCOUNT__EMAIL_VERIFICATION_BY_CODE_ENABLED` | bool | `false` | `ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED` — verify with a typed code instead of a clicked link, for **every** signup. |
| `mfa_webauthn_rp_id` | `ACCOUNT__MFA_WEBAUTHN_RP_ID` | str \| null | `null` | WebAuthn Relying Party ID. `null` derives it per request from the Host header (fine for a single host). Pin to the registrable parent domain (e.g. `arkitekt.live`) when one deployment answers on several subdomains. |
| `mfa_totp_issuer` | `ACCOUNT__MFA_TOTP_ISSUER` | str \| null | `null` | `MFA_TOTP_ISSUER` — the label authenticator apps show. Defaults to the deployment name; allauth's own default is an empty string. |
| `headless_frontend_urls` | `ACCOUNT__HEADLESS_FRONTEND_URLS__*` | object | see below | SPA URLs for allauth-headless flows. |
| `social_provider_apps` | — (use YAML) | list[str] | `["allauth.socialaccount.providers.orcid", "allauth.socialaccount.providers.google"]` | allauth social provider apps appended to `INSTALLED_APPS`. |

#### Passkeys and the Relying Party ID

A passkey is bound to a **Relying Party ID** — a domain. allauth derives it per
request from the `Host` header and offers no setting to override it, so lok
subclasses the MFA adapter (`lok_server/mfa_adapter.py`) to honour
`account.mfa_webauthn_rp_id`.

Leave it `null` on a single-host deployment. Pin it when **one** deployment
answers on several hostnames off one database:

```yaml
account:
  mfa_webauthn_rp_id: example.org   # covers go.example.org AND beta.example.org
```

The value must equal, or be a registrable-domain suffix of, every origin the SPA
is served from — browsers accept `example.org` as the RP ID for a page on
`go.example.org`, but never the reverse, and never a public suffix such as
`.org` itself. Get this wrong and enrolment succeeds while authentication
silently fails on the other host, reported only as a generic "incorrect code".

Passkey **signup** (`mfa_passkey_signup_enabled`) is a bigger commitment than
passkey login: allauth requires `email_verification_by_code_enabled` alongside
it, and that switches email verification from clickable links to typed codes for
every signup on the deployment, leaving
`headless_frontend_urls.account_confirm_email` unused.

#### Username world vs email world

Switching which identifier users log in with is a one-flag change — flip
`account.login_methods` (the SPA login/signup forms adapt automatically):

```yaml
# Username world (default): classic username + password
account:
  login_methods: [username]

# Email world: users provide and log in with an email
account:
  login_methods: [email]        # signup_fields auto-derives to [email*, password1*, password2*]
  # email_verification: optional # optional; add `mandatory` to force confirmation (see below)
```

Notes for the email world:
- Give every seeded `users:` entry an `email:` — otherwise that user has no email
  identifier to log in with. `ensureusers` provisions it as a **verified, primary**
  `EmailAddress`, so seeded users (e.g. `demo`) work even under `mandatory`
  verification without any manual confirmation step.
- With `email_verification: mandatory`, an SMTP `email:` block is **required** (see
  next section) — enforced at config load.

#### Enabling login via email with account verification

Switch login to email and require users to confirm their address before they can
sign in:

```yaml
account:
  login_methods: [email]
  email_verification: mandatory   # block sign-in until the email is verified
  # signup_fields omitted → derived to [email*, password1*, password2*]
email:                            # REQUIRED when verification is mandatory
  host: smtp.example.com
  port: 587
  user: apikey
  password: <secret>
  email: no-reply@example.com
```

Notes:
- `email_verification: mandatory` **requires** an `email:` SMTP block — otherwise
  verification mails can't be sent and users are permanently locked out. Config
  loading fails fast if it is missing.
- `login_by_code_enabled` (on by default) also sends email, but is a **soft**
  dependency rather than a hard failure: when no `email:` block is configured it
  is automatically disabled (the "Send me a sign-in code" option is hidden) instead
  of advertising a flow that can't deliver a code. Password login still works.
- `signup_fields` must collect an email (`email*`) whenever `login_methods`
  includes `email`; this is validated on load.

#### Verifying local signups but trusting social logins

`email_verification` governs **local** (email/password) accounts; social/OIDC
logins are governed separately by `social_email_verification`, which allauth
applies to both new social signups *and* subsequent logins of existing social
accounts. This lets you require verification for password users while trusting an
IdP to vouch for its own:

```yaml
account:
  login_methods: [email]
  email_verification: mandatory        # local signups must confirm their email
  social_email_verification: none      # Google/ORCID/CILogon logins skip our confirmation
  social_email_required: false         # ...and may carry no email at all (e.g. ORCID)
```

Notes:
- `social_email_verification: none` opens the gate for **all** social providers
  globally (allauth has no per-provider verification level). To keep that an
  explicit, per-provider decision, config load **requires every provider under
  `socialaccount_providers` to be marked `VERIFIED_EMAIL: true`** when it is set —
  otherwise a provider (including one added later) would be silently exempted with
  its email stored unverified and no path to verify it.
- `social_email_verification: mandatory` carries the same SMTP dependency as the
  local setting: an `email:` block is required (enforced on load).
- `social_email_verification: mandatory` with `social_email_required: false` is
  rejected — verification needs an address to confirm, so a social login with no
  email could never complete.

#### `account.headless_frontend_urls` — SPA URLs allauth-headless points users at

The `{key}` placeholders are substituted by allauth. These default to **relative
paths** that are joined to [`kontrol_frontend_url`](#top-level-settings) at load
time, so you normally only set that one base URL. Override an individual key here
with a fully-qualified URL (with scheme) to host that flow elsewhere — absolute
values are used verbatim.

| Key | Env var | Type | Default | Description |
|---|---|---|---|---|
| `account_confirm_email` | `ACCOUNT__HEADLESS_FRONTEND_URLS__ACCOUNT_CONFIRM_EMAIL` | str | `/account/verify-email/{key}` | Email-verification link. Joined to `kontrol_frontend_url` unless absolute. |
| `account_reset_password` | `ACCOUNT__HEADLESS_FRONTEND_URLS__ACCOUNT_RESET_PASSWORD` | str | `/account/password/reset` | Password-reset request page. Joined to `kontrol_frontend_url` unless absolute. |
| `account_reset_password_from_key` | `ACCOUNT__HEADLESS_FRONTEND_URLS__ACCOUNT_RESET_PASSWORD_FROM_KEY` | str | `/account/password/reset/key/{key}` | Password-reset-from-key link. Joined to `kontrol_frontend_url` unless absolute. |
| `account_signup` | `ACCOUNT__HEADLESS_FRONTEND_URLS__ACCOUNT_SIGNUP` | str | `/account/signup` | Signup page URL. Joined to `kontrol_frontend_url` unless absolute. |

### `deployment` — human-facing deployment identity

| Key | Env var | Type | Default | Description |
|---|---|---|---|---|
| `name` | `DEPLOYMENT__NAME` | str | `default` | Deployment name. |
| `description` | `DEPLOYMENT__DESCRIPTION` | str | `A Basic Arkitekt Deployment` | Deployment description. |
| `configure_url` | `DEPLOYMENT__CONFIGURE_URL` | str | `/configure/{code}` | URL template the fakts well-known (`/.well-known/fakts`) advertises as its `configure` endpoint. `{code}` is substituted by the client with the device code. |
| `mesh_configure_url` | `DEPLOYMENT__MESH_CONFIGURE_URL` | str | `/meshconfigure/{code}` | URL template the fakts well-known advertises as its `mesh_configure` endpoint (the [mesh device-code](docs/fakts_flows/mesh_device_code.md) page). Resolved to an absolute URL exactly like `configure_url`; `{code}` is substituted by the machine. |
| `hub_configure_url` | `DEPLOYMENT__HUB_CONFIGURE_URL` | str | `/hubconfigure/{code}` | URL template the fakts well-known advertises as its `hub_configure` endpoint (the [hub device-code](docs/fakts_flows/hub_device_code.md) page). Resolved to an absolute URL exactly like `configure_url`; `{code}` is substituted by the client. |

The `configure_url` is what the fakts well-known hands clients to point a user at the
device-code configure page. It is always advertised as an **absolute** URL, resolved
from one of three shapes:

- **root-relative** (`/configure/{code}`, the default) — joined to the deployment's
  base domain (the same base as the deprecated `frontend_url`);
- **absolute with scheme** (`https://go.arkitekt.live/configure/{code}`) — used verbatim;
- **bare host** (`go.arkitekt.live/configure/{code}`) — promoted to `https://`.

Prefer an absolute value when lok sits behind a reverse proxy, since the base domain is
otherwise derived from the incoming request and can be wrong. The well-known's older
`frontend_url` / `base_url` fields are kept for back-compat but are **deprecated** in
favour of the explicit `configure` field.

### `email` — outbound SMTP (optional)

Optional block for outbound email. Omit it entirely to disable email. When present,
`password` is required.

| Key | Env var | Type | Default | Description |
|---|---|---|---|---|
| `host` | `EMAIL__HOST` | str | `NOTSET` | SMTP server host. |
| `port` | `EMAIL__PORT` | int | `587` | SMTP server port. |
| `use_tls` | `EMAIL__USE_TLS` | bool | `true` | Use STARTTLS. |
| `user` | `EMAIL__USER` | str | `NOTSET` | SMTP username. |
| `password` 🔒 | `EMAIL__PASSWORD` | str | **required** (when block present) | SMTP password. |
| `email` | `EMAIL__EMAIL` | str | `NOTSET` | Default `From` address. |

### `ionscale` — tailnet coordinator connection (optional)

Optional connection to an [ionskale](https://github.com/arkitektio/ionskale) tailnet
coordinator. Omit the whole block to disable it. When present, `server_url`,
`coord_url` and one credential (`service_token`, or the legacy `admin_key`) are
required.

Lok talks to ionskale over its HTTP (connect + JSON) API using a **static
service token**: declare one under `auth.service_tokens` in the ionskale config
(`{name: lok, token: "svc_…"}`) and put the same value here. Memberships are
pushed to the tailnet's IAM policy on commit, a removed member is revoked
immediately (`RevokeAccount`), and deleting an organization or its mesh tears
the tailnet down. Whatever a control-plane outage made lok miss is repaired by
`manage.py reconcile_meshes` (`--dry-run`, `--organization <slug|pk>`,
`--revoke-orphans`).

| Key | Env var | Type | Default | Description |
|---|---|---|---|---|
| `server_url` | `IONSCALE__SERVER_URL` | str | **required** | ionskale server URL (the HTTP API; usually the same as `coord_url`). |
| `coord_url` | `IONSCALE__COORD_URL` | str | **required** | Public coordination URL advertised to mesh clients. |
| `service_token` 🔒 | `IONSCALE__SERVICE_TOKEN` | str | `null` | Static service token (`svc_…`) from ionskale's `auth.service_tokens`. Grants system-admin on ionskale; preferred over `admin_key`. |
| `admin_key` 🔒 | `IONSCALE__ADMIN_KEY` | str | `null` | **Legacy.** ionskale system-admin key used to drive the `ionscale` CLI binary. Only consulted when `service_token` is unset; the lok image no longer ships the binary, so a deployment on this path has to mount one itself. |
| `verify_tls` | `IONSCALE__VERIFY_TLS` | bool | `true` | Verify ionskale's TLS certificate (HTTP mode). |
| `timeout` | `IONSCALE__TIMEOUT` | float | `10.0` | Per-request timeout in seconds (HTTP mode). |
| `magic_dns_suffix` | `IONSCALE__MAGIC_DNS_SUFFIX` | str | `null` | MagicDNS suffix served by ionskale (mirrors its `dns.magic_dns_suffix`); used to render a machine's MagicDNS name as `<name>.<suffix>`. |
| `auto_create_mesh` | `IONSCALE__AUTO_CREATE_MESH` | bool | `true` | Provision a mesh (one tailnet, bound to the organization) for every new organization. When `false`, meshes are only created on explicit opt-in. |
| `repository` | `IONSCALE__REPOSITORY` | str | `null` | Dotted path to an `IonscaleRepo` factory (tests). Satisfies the credential requirement on its own. |
| `eager_init` | `IONSCALE__EAGER_INIT` | bool | `false` | Build the ionscale client on boot, so a broken configuration fails at startup rather than on first use. |

> **Upgrading from `admin_key`:** set `ionscale.service_token` (and the matching
> `auth.service_tokens` entry on ionskale) *before* pulling a lok image without
> the bundled binary. On the legacy path a missing binary raises
> `FileNotFoundError` on first mesh operation (or at boot with `eager_init`).

### Top-level OIDC / provisioning fields

These live at the **root** of the config (not inside a block), so their environment
variable is just the upper-cased field name (e.g. `private_key` → `PRIVATE_KEY`). The
list/object fields below are provisioning data applied on boot — express them in YAML.

| Key | Env var | Type | Default | Description |
|---|---|---|---|---|
| `private_key` 🔒 | `PRIVATE_KEY` | str (PEM) | **required** | OIDC/OAuth2 RSA private signing key. Lok refuses to start without it. |
| `oidc_issuer` | `OIDC_ISSUER` | str | `http://lok` | OIDC issuer URL advertised by lok. |
| `kontrol_frontend_url` | `KONTROL_FRONTEND_URL` | str | `/` | Base URL of the kontrol SPA. All account email links (verify-email, password reset, signup) and invite/organization redirects derive from it. Set to the deployment's frontend origin, e.g. `https://go.arkitekt.live`. |
| `privacy_guards` | `PRIVACY_GUARDS` | str | `opt-in` | Policy for *integrated* login widgets (e.g. Google One Tap) — `strict` / `opt-in` / `disabled`. See [Integrated login widgets](#integrated-login-widgets-google-one-tap). |
| `socialaccount_providers` | — (use YAML) | map[str, provider] | `{}` | `SOCIALACCOUNT_PROVIDERS`, keyed by provider id. Typed — see [Social login providers](#social-login-providers). |
| `organizations` | — (use YAML) | list[object] | `[]` | Organizations ensured on boot. |
| `users` | — (use YAML) | list[object] | `[]` | Users ensured on boot. |
| `memberships` | — (use YAML) | list[object] | `[]` | User/organization memberships ensured on boot. |
| `redeem_tokens` | — (use YAML) | list[object] | `[]` | Redeem tokens provisioned on boot. |
| `kommunity_partners` | — (use YAML) | list[object] | `[]` | Pre-authorized kommunity partner apps. |
| `system_messages` | — (use YAML) | list[object] | `[]` | System messages shown to users. |
| `openid_apps` | — (use YAML) | list[openid_app] | `[]` | OIDC/OAuth2 clients provisioned on boot. |

#### `openid_apps[]` — OIDC/OAuth2 clients provisioned on boot

Each entry provisions an OAuth2 client (see the `ensureopenid` command). Provide the
client(s), secret(s) and redirect URIs **per deployment** — none are created by default.

**Every OIDC relying party that logs in through lok (the SPA, ionscale, external
apps) needs an entry here whose `client_id` and `client_secret` match that
service's own config.** If it's missing, lok's OpenID flow fails with **"client
does not exist"**. See [docs/openid_clients](./docs/openid_clients/README.md) for
the full setup, the values that must match across services, and troubleshooting.

| Key | Type | Default | Description |
|---|---|---|---|
| `client_name` | str | **required** | Human-readable client name. |
| `client_id` | str | **required** | OAuth2 `client_id`. Must match the relying party's `client_id`. |
| `client_secret` 🔒 | str | **required** | OAuth2 client secret. Must match the relying party's secret. Override per deployment. |
| `redirect_uris` | list[str] | `[]` | Allowed OAuth2 redirect URIs (the relying party's callback URL). |
| `email_template` | str | `null` | Template for the `email` claim, rendered per user from membership variables (e.g. `"{username}@corp.example"`). Available variables: `username`, `user_id`, `email`, `membership_id`, `org_slug`, `org_name`. Validated at boot — an unknown variable or attribute access (e.g. `{user.email}`) fails config load. When unset, the user's own email is used (falling back to a synthetic `<pk>@users.noreply` address). |

---

## Social login providers

Lets users sign in with Google, GitHub, ORCID, etc. via
[django-allauth](https://docs.allauth.org/). Adding a provider is **two coupled
steps** — miss either and the button won't appear or login will 500:

1. **Install the provider app** — add its dotted path to
   `account.social_provider_apps` (appended to `INSTALLED_APPS`).
2. **Configure its OAuth app** — add a typed entry under
   `socialaccount_providers`, keyed by the provider id, carrying the `client_id`
   / `secret` you got from the provider (and any scopes).

```yaml
account:
  social_provider_apps:
    - allauth.socialaccount.providers.google
    - allauth.socialaccount.providers.github

socialaccount_providers:
  google:
    APP:
      client_id: "1234567890-abc.apps.googleusercontent.com"
      secret: "GOCSPX-your-secret"   # 🔒 set per deployment (env: SOCIALACCOUNT_PROVIDERS__… or a secret file)
    SCOPE: [profile, email]
    AUTH_PARAMS: { access_type: online }
    OAUTH_PKCE_ENABLED: true
  github:
    APP:
      client_id: "Iv1.your-app-id"
      secret: "your-github-secret"   # 🔒
    SCOPE: [read:user, user:email]
```

### `socialaccount_providers.<provider>` — typed fields

Common keys are validated (a mistyped `client_id` or unknown `APP` key is
rejected at boot with the exact path); provider-specific extras (e.g.
`FETCH_USERINFO`) are still accepted verbatim.

| Key | Type | Description |
|---|---|---|
| `APP` | object | A single OAuth app credential (below). Use this for one app. |
| `APPS` | list[object] | Multiple app credentials — rarely needed. |
| `SCOPE` | list[str] | OAuth scopes to request. |
| `AUTH_PARAMS` | map | Extra query params on the authorize request. |
| `OAUTH_PKCE_ENABLED` | bool | Enable PKCE where supported (recommended). |
| `VERIFIED_EMAIL` | bool | Treat the provider's email as already verified. |
| `EMAIL_AUTHENTICATION` | bool | Match logins to existing accounts by email. |

`APP` (and each entry of `APPS`) — field names match allauth exactly:

| Key | Type | Default | Description |
|---|---|---|---|
| `client_id` | str | **required** | OAuth client id / consumer key from the provider. |
| `secret` 🔒 | str | `""` | OAuth client secret. Set per deployment. |
| `key` | str | `""` | Extra key a few providers require; usually blank. |
| `name` | str | provider id | Human-readable app name. |
| `provider_id` | str | — | Sub-provider instance id (OpenID Connect / SAML only). |
| `settings` | map | `{}` | Provider-app-specific settings blob. |

Once configured, the provider's button shows on the SPA login/signup pages
automatically (the frontend reads the enabled providers from the allauth
capability config). The **redirect / callback URL** to register with the
provider is `https://<your-host>/lok/accounts/<provider>/login/callback/`.

> **⚠️ 2026 disclaimer.** OAuth provider consoles, endpoint URLs, required
> scopes, and app-verification/review policies change frequently and differ per
> provider. The snippets above are illustrative and were accurate as of **2026**
> — always follow the provider's current developer documentation and
> [allauth's per-provider docs](https://docs.allauth.org/en/latest/socialaccount/providers/index.html)
> for the exact `client_id`/`secret` fields, scopes, and callback-URL format,
> and treat every `secret` as sensitive (inject via environment or a secret
> file, never commit it).

### SAML (institutional single sign-on)

SAML uses the same two steps, with the `saml` extra installed
(`django-allauth[…,saml]`). It is **server configuration only** — there is no
self-service IdP registration — and it is purely an authentication method:
**organizations are orthogonal to it.** A SAML login registers or signs in a user;
it grants no organization membership, and joining an organization still goes
through the ordinary invite flow.

```yaml
socialaccount_providers:
  saml:
    APPS:
      - client_id: acme-university      # IdP name + URL segment. NOT an organization.
        provider_id: "saml:acme-university"   # == SocialAccount.provider — immutable
        name: "Acme University"
        settings:
          verified_email: ["acme.edu", "student.acme.edu"]
          attribute_mapping: {uid: [...], email: [...]}
          idp: {metadata_url: "https://idp.acme.edu/idp/shibboleth"}
```

Endpoints to register with the IdP are
`https://<your-host>/lok/accounts/saml/<client_id>/{acs,metadata,sls}/` — note this
differs from the OAuth callback URL above.

One rule differs from OAuth providers: when `account.social_email_verification` is
`none` and a provider configures **more than one app**, each app must set
`settings.verified_email` to a non-empty **list of domains**. A bare `true` is
rejected, since it would let one institution's IdP mark another's domain as
verified. Matching is exact, so enumerate every subdomain. See
[docs/social_accounts/README.md §6](docs/social_accounts/README.md) for the full
picture, including why `EMAIL_AUTHENTICATION` is deliberately left off.

### Integrated login widgets (Google One Tap)

Some providers offer an *integrated* sign-in widget — most notably **Google One
Tap** — that loads a third-party script (`accounts.google.com/gsi/client`) and can
identify or track the visitor **before they click anything**. This is a different
privacy profile from the ordinary redirect buttons above, which only do anything
on an explicit click. `privacy_guards` controls how the SPA treats these widgets;
it does **not** affect the normal redirect provider buttons.

| Value | Behaviour |
|---|---|
| `strict` | The widget is never rendered and its third-party script is never loaded. |
| `opt-in` *(default)* | The SPA shows a "Mind your privacy" consent prompt first and only loads the script after the user clicks *Enable*. |
| `disabled` | Guards off — the widget loads immediately, with no consent prompt. |

The value is advertised to the SPA on the allauth headless capability config
(`/lok/_allauth/browser/v1/config`, key `privacy_guards`), so changing it takes
effect on the frontend without a rebuild. Enabling Google One Tap itself is still
the two-step provider setup above (install the app + configure its OAuth `APP`);
`privacy_guards` only decides whether the SPA is allowed to surface it.

---

## Minimal example

```yaml
django:
  secret_key: "REPLACE_ME"
  debug: false
  admin:
    username: admin
    password: "REPLACE_ME"
    email: admin@example.com
postgres:
  db_name: lok
  username: lok
  password: "REPLACE_ME"
  host: db
  port: 5432
redis:
  host: redis
  port: 6379
authentikate:
  issuers:
    - kind: rsa
      iss: lok
      kid: lok-key-1
      public_key: "ssh-rsa AAAA..."
  static_tokens: {}
datalayer:
  access_key: "REPLACE_ME"
  secret_key: "REPLACE_ME"
  host: minio
  port: 9000
  protocol: http
  media:
    bucket: lok-media
private_key: |
  -----BEGIN PRIVATE KEY-----
  ...
  -----END PRIVATE KEY-----
```

Validate it with `python manage.py validate_settings`.
