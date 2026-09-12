# Redeem token — the headless fakts grant

Provisions a client **non-interactively** from a pre-shared one-time token
(CI, headless installs). Same combined response as the
[canonical device-code grant](./client_device_code.md) — access token, refresh
token and rendered instances in one exchange — but with no human step at
grant time: the authorization happened when a member minted the redeem token.

## 1. Minting a redeem token

There are two mint endpoints, for two callers:

- **Management GraphQL (kontrol, human):** `createRedeemToken(input: {hub,
  expiresInDays})`. The hub is required and **carries the organization** — the
  minting user must be a member (owner/admin), and every client redeemed from the
  token lands in that hub's organization, bound to the minting user's membership.
- **App-facing GraphQL (an app such as a deployer):** `createRedeemToken(input:
  {manifest!, expiresInDays, maxRedemptions})`. The hub is the *calling client's*
  hub. `expiresInDays` defaults to 7 and is capped at 30; `maxRedemptions` is
  unlimited when omitted. `manifest` is **required**: a token minted by an app is
  always pre-authorized for one manifest — see §5. (Only operator-provisioned tokens
  from the deployment config and the management API are unpinned.)

## 2. Redeeming: `POST /o/token/`

Standard OAuth2 form encoding, custom grant type:

```
grant_type=urn:fakts:grant-type:redeem
redeem_token=…
manifest={"identifier": "com.example.app", "version": "1.0.0", "scopes": [], "requirements": []}
requested_client_role=agent        (optional, default interface)
```

`manifest` is the JSON-serialized [`Manifest`](./README.md#manifest). There is
no client authentication — the redeem token *is* the credential; the fakts
client and its public OAuth2 client are provisioned (or reused) during the
exchange.

On success the response is the same combined token response as the device-code
grant (`access_token`, `refresh_token`, `expires_in`, `scope`, plus
`client_id`, `self`, `instances`, `statuses`). Failures are standard OAuth
errors: HTTP 400 with `error=invalid_grant` (unknown/expired token, or a
changed manifest without `allow_reredeem` — see below) or
`error=invalid_request` (missing/malformed fields).

## 3. Re-redeem semantics

- Redeeming again with the **same manifest** returns the same client (a fresh
  token pair each time).
- A **changed manifest** is rejected unless the token was created with
  `allow_reredeem` — then the client is re-validated against the new manifest.
- An **expired** token is deleted on first use after expiry.

## 4. After the grant

Identical to the device-code grant: refresh with
`grant_type=refresh_token` + `refresh_token` + `client_id` (rotating, 30-day
window, envelope re-rendered on every refresh), authenticate everywhere with
the Bearer JWT.

## 5. Pre-authorized (pinned) tokens

A token minted through the app-facing API is **pinned** to its `manifest` at mint
time and stores the pin as `pinnedManifest`. On *every* redeem, before anything is looked up or
provisioned, the presented manifest must satisfy the pin:

| field | rule |
|---|---|
| `identifier`, `version` | must match exactly |
| `node_id` | must match exactly when the pin carries one; a pin without `node_id` accepts any node |
| `scopes` | ceiling — the presented manifest may request a subset, never a scope outside the pin |
| `requirements` | ceiling on `(key, service)` pairs — extra requirements would render extra service instances into the envelope |
| everything else (title, description, logo, authors, keywords, public sources) | not compared; the running app assembles these from its image |

A deviation is `invalid_grant` with an `error_description` naming the pinned
value, and no client is created. This is what makes a token safe to hand to an
unattended container: whoever holds it can only ever enrol as the one app the
minting party approved, on the node it approved, with at most the rights it
approved. The first-redeem `manifest_hash` pin (§3) still applies on top.

A typical deployer *spends* one pinned token per container: mint it with
`expiresInDays: 1, maxRedemptions: 1` immediately before starting the container,
pass it as `FAKTS_REDEEM_TOKEN`, wait until `redeemToken(id).client` reports the
client it produced, check that client is the approved app, and then revoke the
token with `deleteRedeemToken(input: {id})`. From then on the container lives on
its refresh chain and the value left in its environment is worthless. A container
whose refresh chain has lapsed (stopped longer than the refresh window) gets a fresh
pinned token and a new container, not a re-redeem.

`deleteRedeemToken` is scoped like `redeemToken(id)`: only the issuing user, inside
their active organization. Revoking a token never touches a client it already
produced.
