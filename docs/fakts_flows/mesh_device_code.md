# Mesh device code ("meshconfigure")

Lets a **standalone machine join the organization's mesh** (its ionscale tailnet)
with a configurable machine name. The machine asks to join; a human member picks the
organization, confirms the name it should join under, and approves; the machine
polls and receives a single-use pre-authorized key plus the mesh coordination URL,
then runs `tailscale up --authkey=<key> --hostname=<machine_name>`.

- [When to use](#when-to-use)
- [Why](#why)
- [Protocol](#protocol)
- [Sendable params — machine (REST)](#sendable-params--machine-rest)
- [Sendable params — authorizer (GraphQL)](#sendable-params--authorizer-graphql)
- [Responses](#responses)
- [Machine name is a hint, not enforced](#machine-name-is-a-hint-not-enforced)
- [Code path](#code-path)

## When to use

- You have a bare machine (a GPU box, an edge node, a CI runner) that should join
  the org's private mesh, and you want a human to authorize it.
- You want to name the node at join time rather than baking a hostname into an
  image.

Use the [hub flow](./hub_device_code.md) instead when the mesh key
is part of standing up a whole hub (that flow mints a *hub-scoped* key as a
side effect); use this flow when joining a machine is the *only* thing you want.

## Why

Adding a machine to a private network is exactly the kind of grant that should not
be self-serve from an unauthenticated device: the machine proves nothing, so an
authenticated org member authorizes it — the same trust model as the existing
`createIonscaleAuthKey` mutation, but initiated by the machine and packaged as a
device-code flow. The minted key is **single-use** (one machine per code) and
**persistent** (the node stays registered after it disconnects).

Like the service/hub flows it uses a **separate `challenge_code`**. Unlike
them it does **not** use `/f/claim/`: the credential a bare mesh join needs is just
the key + coord URL + name, so the challenge poll returns those directly.

## Protocol

1. **Discover** — `GET /.well-known/fakts` for `mesh_device_code_start`,
   `mesh_challenge_url`, `mesh_configure`, and `mesh_coord_url` (see
   [discovery](./discovery.md)).
2. **Start** — `POST /f/meshstart/` with the requested name ⇒
   `{status: granted, code, challenge}`.
3. **Configure** — show the user the configure URL with `{code}`
   (`/meshconfigure/<code>`); they pick the org, confirm/edit the machine name, and
   authorize. Accept mints an `IonscaleAuthKey` against the org's mesh.
4. **Poll** — `POST /f/meshchallenge/` with `{code: <challenge>}` until `granted`,
   then read `ionscale_auth_key`, `ionscale_coord_url`, `machine_name`.
5. **Join** — `tailscale up --login-server=<coord_url> --authkey=<key>
   --hostname=<machine_name>`.

## Endpoints

| Step | Method | Path | URL name |
|---|---|---|---|
| Start | POST | `/f/meshstart/` | `fakts:meshstart` |
| Challenge (poll) | POST | `/f/meshchallenge/` | `fakts:meshchallenge` |

## Sendable params — machine (REST)

### Start — `MeshDeviceCodeStartRequest`
`POST /f/meshstart/`

| Field | Type | Default | Description |
|---|---|---|---|
| `requested_machine_name` | str? | `null` | The name the machine suggests; the authorizer sees it pre-filled and may edit it. |
| `description` | str? | `null` | Human-readable purpose, shown on the configure page. |
| `ephemeral` | bool | `false` | Requested ephemerality. **Note:** advisory only — the authorizer's GraphQL input decides the key type; the acceptor's choice wins. |
| `tags` | str[] | `[]` | Requested ionscale ACL tags. **Note:** advisory only — same as above. |
| `expiration_time_seconds` | int | `600` | How long the code stays valid. |

### Challenge — `DeviceCodeChallengeRequest`
`POST /f/meshchallenge/`

| Field | Type | Default | Description |
|---|---|---|---|
| `code` | str | — (required) | The `challenge` value returned by start. |

## Sendable params — authorizer (GraphQL)

The configure page looks the code up with `meshDeviceCodeByCode(code)`. That type
**deliberately does not expose the minted key** — the secret is delivered only via
the REST poll, so a preview-friendly by-code lookup can't leak a live join
credential.

### `acceptMeshDeviceCode(input: AcceptMeshDeviceCodeInput!) → ManagementMeshDeviceCode`

| Field | Type | Default | Description |
|---|---|---|---|
| `deviceCode` | ID | — (required) | The mesh device code's **id**. |
| `organization` | ID | — (required) | Organization whose mesh to join. The caller must be a member; the org must already have a mesh (this flow will not create a tailnet). |
| `machineName` | str? | `null` | Final machine name. Falls back to the code's `requestedMachineName` when omitted. |
| `ephemeral` | bool | `false` | Whether the minted node is ephemeral. |
| `tags` | str[]? | `null` | ACL tags for the key. Defaults to `["tag:mesh-<org.pk>"]` when omitted. |

### `declineMeshDeviceCode(input: DeclineMeshDeviceCodeInput!) → ManagementMeshDeviceCode`

| Field | Type | Default | Description |
|---|---|---|---|
| `deviceCode` | ID | — (required) | The mesh device code's id; marks it denied. |

## Responses

| Endpoint | Success | Non-success |
|---|---|---|
| `/f/meshstart/` | `{status: granted, code, challenge}` | `{status: error, error}` |
| `/f/meshchallenge/` | `{status: granted, ionscale_auth_key, ionscale_coord_url, machine_name}` | `{status: pending\|denied\|expired}` · `{status: error, error: "Challenge does not exist"}` |

## Machine name is a hint, not enforced

lok never renames a mesh node and an ionscale pre-auth key cannot carry a hostname,
so `machine_name` is a **hint**: lok stores it and returns it in the poll, and the
joining client is expected to use it as `--hostname`. lok does not force the node's
name server-side; a client that ignores the hint joins under whatever name it
reports.

## Code path

- REST: `MeshStartChallengeView` / `MeshChallengeView` (`fakts/views.py`). The mesh
  challenge does **not** use `_poll_device_code` — it returns the key/coord/name
  payload directly.
- Service: `start_mesh_device_code` (`fakts/services/device_codes.py`),
  `create_mesh_auth_key` (`fakts/services/hubs.py`).
- Model: `MeshDeviceCode` (`fakts/models.py`).
- GraphQL: `accept_mesh_device_code` / `decline_mesh_device_code`
  (`api/management/mutations/mesh_device_code.py`); `mesh_device_code_by_code` query
  and `ManagementMeshDeviceCode` type.
- Frontend: `src/mesh/MeshConfigurePage.tsx`, route `/meshconfigure/:meshCode`.
- Config: `mesh_configure_url` (`DEPLOYMENT__MESH_CONFIGURE_URL`), advertised as
  `mesh_configure` in the well-known.
