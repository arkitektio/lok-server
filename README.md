# lok-server

The identity provider and configuration server of an [Arkitekt](https://arkitekt.live) hub.
Lok signs users in, issues the OAuth2 / OpenID Connect tokens every other service verifies,
and hands apps and services their configuration through the Fakts protocol. Because its
tokens are signed JWTs, a service checks them against lok's published keys and needs no
shared session store. It is registered as `live.arkitekt.lok`; its web UI is
[kontrol](https://github.com/arkitektio/kontrol).

## What it stores

| App | Concepts |
| --- | --- |
| `karakter` | Who exists: `User`, `Organization`, `Membership`, `Role`, `Scope`, invites and membership requests. |
| `fakts` | What is installed: `App`, `Release` and `Client` (an app on a device), `Service`, `ServiceInstance` and its aliases, `Hub`, `Device`, device codes, redeem tokens, mandates. |
| `authapp` | Issued tokens, authorization codes and used nonces. |
| `ionscale` | No tables. It keeps each organization's WireGuard mesh in [ionskale](https://github.com/arkitektio/ionskale) in step with lok's memberships. |

Almost everything belongs to an organization. A token carries the user, the client and the
active organization, and that is what the other services scope their data by.

## API

| Path | What it serves |
| --- | --- |
| `/graphql`, `/schema` | The GraphQL API apps use (`lok_server/schema.py`): the caller's identity and context, organizations, members and invites, clients, services, hubs, mandates. |
| `/managementgraphql/`, `/managementschema/` | The management schema kontrol uses (`api/management/schema.py`), session-authenticated. |
| `/o/` | OAuth2: `token/`, `authorize/`, `revoke/`, `jwks/`, `user_info/`. |
| `/f/` | Fakts: the flows by which an app, a service, a hub or a mesh device obtains its configuration and credentials. |
| `/.well-known/` | `fakts`, `openid-configuration`, `oauth-authorization-server`, `jwks.json`, `hub-keys/<id>`. |
| `/_allauth/` | Headless [django-allauth](https://allauth.org): login, signup, MFA and passkeys, social and SAML accounts. |
| `/ht`, `/admin/` | Health check and Django admin. |

The guides in [docs/](docs/README.md) explain the parts that need explaining: the
[fakts flows](docs/fakts_flows/README.md), [OpenID clients](docs/openid_clients/README.md)
and [social login](docs/social_accounts/README.md).

## Running

The image is `jhnnsrs/lok`. It has no default command; start it with `arkitekt-service serve`. Unlike
the other services, lok prepares itself on every start: it waits for the database, migrates,
then ensures what the configuration declares (partners, OpenID apps, users, organizations,
memberships, redeem tokens) before it serves on :80 with daphne. `arkitekt-service debug` does the same
with Django's autoreloading server.

It needs Postgres, Redis and an S3 store (RustFS) for avatars and banners. An ionskale
server and SMTP are optional. `python manage.py reconcile_meshes` (`--dry-run`) brings the
meshes back in line with lok by hand.

## Configuration

The service reads `config.yaml`, or the file named by `ARKITEKT_CONFIG_FILE`; any value can
be overridden by an environment variable (`POSTGRES__HOST`). `config.yaml` is not tracked,
because it holds the key lok signs tokens with; start from
[`config.example.yaml`](config.example.yaml). Lok refuses to start on a key that is
committed to this repository. `python manage.py validate_settings` prints the configuration
as the service reads it, with secrets redacted.

See [CONFIG.md](CONFIG.md) for every value.

## Development

```sh
uv sync
uv run pytest
```

The suite runs against a real stack, brought up by [dokker](https://github.com/jhnnsrs/dokker)
from `tests/integration/docker-compose.yaml`: Postgres (`jhnnsrs/daten:next`), RustFS with
its buckets created by `jhnnsrs/init:next`, and Redis, on ports Docker picks. Nothing is
mocked. It needs a running Docker daemon. The suite's configuration is
`tests/config.test.yaml`, and `tmanage.py` is `manage.py` with the test settings.

## Releases

Releases are tags: a push to `main` cuts a stable version, a push to `next` a release
candidate. Each one publishes `jhnnsrs/lok` under its version (`X.Y.Z`, `X.Y`, `X`), plus
`latest` from `main` and `next` from `next`. The `version` in `pyproject.toml` is a
placeholder. Release notes are on
[GitHub Releases](https://github.com/arkitektio/lok-server/releases); `CHANGELOG.md` is
frozen.
