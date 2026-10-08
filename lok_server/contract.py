"""What this image answers a hub's installer: ``arkitekt-service <verb>`` (see ``arkitekt_service.contract``).

Lok is the coordination server, not one of a hub's services: a hub that runs its own writes
Lok's config itself, because that config *is* the hub (its organization, its accounts, the
manifest it starts out with). So this release does not render one. What it does answer is
``migrate``: everything its database needs before it serves, as the one job an installer runs
once per build. Its own start serves, and does nothing else.
"""

from __future__ import annotations

from arkitekt_service.contract import JSON, Contract, Description, Facts, Job, Needs, Refused, Sidecar, Start

from lok_server.configuration import Settings


def render(facts: Facts) -> dict[str, JSON]:
    """Refused: what Lok is configured with is the installer's to say, not a hub's facts'."""
    raise Refused("Lok's config is written by whoever sets the deployment up: it holds the accounts and the hub itself")


contract = Contract(
    description=Description(
        name="lok",
        identifier="live.arkitekt.lok",
        summary="Accounts, organizations and the tokens every service trusts.",
        needs=Needs(storage=["media"]),
        # The mesh control server: Lok creates a private network per organization in it and
        # keeps it in step with the memberships (`ionscale` in the config). Lok runs without
        # it, so it is only started on a hub that has a mesh. A rolling image: it has no
        # versioned tags.
        sidecars=[
            Sidecar(
                name="ionskale",
                image="jhnnsrs/ionskale:latest",
                summary="The mesh control server: one private network per organization, driven by Lok.",
                optional=True,
            )
        ],
    ),
    settings=Settings,
    render=render,
    # How this service is started: there is no script beside it. `arkitekt-service serve`
    # (and `debug`) become these, so they get the container's signals themselves.
    serve=Start(("daphne", "-b", "0.0.0.0", "-p", "80", "--websocket_timeout", "-1", "lok_server.asgi:application")),
    # The development server answers plain HTTP, which the OAuth endpoints otherwise refuse.
    debug=Start(("python", "manage.py", "runserver", "0.0.0.0:80"), {"DJANGO__ALLOW_INSECURE_TRANSPORT": "true"}),
    # What a deployment starts out with, from its config. Each is safe to run again, and
    # the order is theirs: partners before the organizations that are configured from them,
    # users before the memberships and tokens that name them.
    jobs={
        "ensurepartners": Job(("ensurepartners",), "Register the partners the config names"),
        "ensureopenid": Job(("ensureopenid",), "Register the OpenID providers the config names"),
        "ensureusers": Job(("ensureusers",), "Create the accounts the config names"),
        "ensureorganizations": Job(("ensureorganizations",), "Create the organizations the config names, with the hubs their partners bring"),
        "ensurememberships": Job(("ensurememberships",), "Put the accounts into their organizations"),
        "ensuretokens": Job(("ensuretokens",), "Provision the redeem tokens the config names"),
        "reconcile_meshes": Job(("reconcile_meshes",), "Repair drift between the meshes and ionscale: --dry-run, --organization, --revoke-orphans, --sidecars"),
    },
    setup=("ensurepartners", "ensureopenid", "ensureusers", "ensureorganizations", "ensurememberships", "ensuretokens"),
)
