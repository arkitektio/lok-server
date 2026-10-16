# Mandate key — the headless fakts grant

Logs an app in **without a human at login time**: CI, containers, a plugin engine
beside a hub, an app a deployer starts. The authorization happened earlier, when a
*mandate* was issued: a standing authority to act as its grantor, inside one hub, up
to a pinned app. Whoever holds the mandate's key logs the app in as the grantor.

Unlike the [device-code grant](./client_device_code.md) there is **no refresh
token**. The app runs the grant again whenever its access token expires (an hour
by default, the organization's `access_token_lifetime` otherwise), and every run
checks the mandate again, so a revoked mandate stops working within one access-token
lifetime.

## 1. Where mandates come from

Every mandate is issued through one policy (`fakts.services.mandates.issue_mandate`),
and **dies with whoever issued it**:

| origin | who issues | acts as | pin | ceiling | dies with |
|---|---|---|---|---|---|
| `member` | a member in kontrol (`issueMandateKey`, management API) | that member | an app (owners/admins may leave it open: the first app that logs in fixes it) | the organization's scopes | the member's membership |
| `client` | an app (`issueMandateKey` / `createMandate`, app API) | the app's user | required | the app's **own** scopes and requirements | the issuing app (rejected, deleted, its refresh chain caught reused) |
| `agent` | the agent an agent mandate names (`issueChildMandateKey`) | the agent mandate's grantor | the parent's pin, narrowed to one device | the parent's pin; never outlives the parent | the parent mandate, and the agent client that issued it |
| `hub` | approving a hub whose manifest asks for the client | the approver (the org owner for a partner-configured hub) | the request's app, any version | the organization's scopes | the hub, or the request leaving the manifest |
| `config` | the `ensuremandates` setup job (`mandate_keys` in the config) | the configured user | optional | the organization's scopes | the config entry |

A mandate's *claimant* is either a **key** (whoever holds it logs in) or an **agent**
(the app it names may issue keyed children under it — kabinet's release approvals are
agent mandates). Revoking a mandate revokes every child issued under it and deletes
their clients; the organization kill switch revokes every mandate of the organization.
lok keeps only a key's SHA-256: a key lok mints is shown once, in the answer that
issued it; a hub or the config proposes its own. A revoked key never works again.

## 2. Logging in: `POST /o/token/`

```
grant_type=urn:fakts:grant-type:mandate-key
key=…
manifest={"identifier": "com.example.app", "version": "1.0.0", "scopes": [], "requirements": []}
requested_client_kind=development   (optional: development | desktop | mobile)
requested_client_role=agent          (optional, default interface)
request_auth_key=true                (optional: a mesh key, if the mandate allows one)
```

The response is `access_token`, `expires_in`, `scope`, plus the fakts envelope
(`client_id`, `self`, `instances`, `statuses`) and, when asked for and allowed, a
`mesh` block. There is never a `refresh_token`. Failures are OAuth errors: 400 with
`invalid_grant` (unknown, revoked or expired key; a manifest outside the pin; the
organization requires a device id), `invalid_scope`, or `invalid_request`.

## 3. The pin

| field | rule |
|---|---|
| `identifier`, `version`, `device_id` | must match exactly when pinned; unpinned means any |
| `scopes` | ceiling — a subset, never more |
| `requirements` | ceiling on `(key, service)` pairs |
| everything else (title, logo, …) | not compared; the running app assembles these from its image |

## 4. The client

One client per mandate and device: logging in again is the same `client_id`, and a
new release of the app updates the same client in place. It acts as the grantor but
never replaces the grantor's own login of the same app. `max_clients` caps on how
many devices one key logs in.

A **new key for a device keeps the device's client.** On a device's first login with
a key, lok takes over the client the device had under an *older* mandate of the same
grantor and origin, for the same app identifier and hub. The `client_id` stays the same, and so
does the device's identity in the services (a rekuest agent is one per client).
Re-keying a device, whether its key expired or a fresh child key was issued under an
agent mandate, is therefore not a new installation. The older key cannot take the
client back: logging in with it again makes a client of its own. A revoked mandate's
clients are deleted with it, so after a revoke the next key is a new client. Without
a `device_id` nothing is taken over.
