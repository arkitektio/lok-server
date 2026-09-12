# Report

> Shared step — **telemetry** a configured client sends back, not a flow that
> obtains a credential.

After a client has claimed and is running, it can post a self-report: whether it is
functional and whether each of its resolved aliases actually worked. lok records the
most recent reports so operators can see, in kontrol, which clients are healthy and
which alias resolutions are failing.

- [When to use](#when-to-use)
- [Why](#why)
- [Sendable params](#sendable-params)
- [Responses](#responses)
- [Code path](#code-path)

## When to use

Periodically from a running client, and especially after it verifies its aliases.
The endpoint is `/f/report/` on the deployment's fakts base URL, authenticated
with the client's Bearer access token (obtained through the
[canonical grant](./client_device_code.md)).

## Why

A registration says a client *should* be able to reach its services; a report says
whether it actually can. Feeding real reachability back to the server turns the
kontrol UI from a static picture of intended topology into a live health view, and
lets alias problems surface where they can be fixed. lok keeps only the most recent
N reports per client (`CLIENT_REPORT_RETENTION`) so the signal stays current.

## Sendable params

### `ReportRequest`
`POST /f/report/` with header `Authorization: Bearer <access_token>` — the
reporting client is identified by the JWT's `client_id` claim, not by a payload
token.

| Field | Type | Default | Description |
|---|---|---|---|
| `alias_reports` | map<str, [`AliasReport`](#aliasreport)> | `{}` | Per-alias reachability results, keyed by alias id. |
| `functional` | bool | `true` | Whether the client considers itself functional overall. |

#### `AliasReport`
| Field | Type | Default | Description |
|---|---|---|---|
| `alias_id` | str? | `null` | The alias this result is for. |
| `valid` | bool | — (required) | Whether the alias resolved/worked. |
| `reason` | str? | `null` | Why it failed, when it did. |

## Responses

| Endpoint | Success | Non-success |
|---|---|---|
| `/f/report/` | `{status: reported, message}` | HTTP 401 with `{status: error, message}` for a missing/invalid/expired Bearer token or unknown client; `{status: error, message}` otherwise. |

## Code path

- REST: `ReportView` (`fakts/views.py`); Bearer verification in `authapp/bearer.py`.
- Service: `report_client` (`fakts/services/clients.py`); retention
  `CLIENT_REPORT_RETENTION` (`lok_server/settings.py`).
- Models: `ReportRequest`, `AliasReport` (`fakts/base_models.py`).
