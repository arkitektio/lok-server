# Lok documentation

Developer documentation for the **lok** server, organized into modules. Each
module explains one area of the system: how it works, how it surfaces in the SPA
(kontrol), and how to configure it.

For the exhaustive, field-by-field configuration reference see
[`../CONFIG.md`](../CONFIG.md). These module docs are the narrative companion to
that reference.

## Modules

| Module | What it covers |
|---|---|
| [social_accounts](./social_accounts/README.md) | Social login (Google, GitHub, ORCID, …): how it works, how it appears at login, how to configure it, and which providers are supported. |
| [openid_clients](./openid_clients/README.md) | Registering OIDC relying parties (the SPA, ionscale, …) via `openid_apps`, the values that must match across services, and fixing "client does not exist". |
| [fakts_flows](./fakts_flows/README.md) | The fakts protocols a client uses to obtain its configuration and credentials at runtime: the client/service/hub/mesh device-code flows, redeem, retrieve, and the shared discovery/claim/report steps — with when-to-use and full sendable params per flow. |

> More modules will be added here over time. Keep each module self-contained and
> link into `CONFIG.md` for the raw settings rather than duplicating tables.
