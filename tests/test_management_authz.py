"""End-to-end authorization tests for the management GraphQL endpoint.

These drive the real URL (`/lok/managementgraphql/`) through Django's async test
client rather than calling ``schema.execute`` directly, so they exercise the
actual view, middleware and context construction:

* the context is strawberry's ``StrawberryDjangoContext`` wrapping a real
  ``HttpRequest`` (not kante's ``UniversalRequest``), and
* ``request.user`` is Django's lazy session-backed user, whose resolution hits
  the DB — which is why ``RequireAuthenticationExtension`` must read it through
  ``sync_to_async`` instead of touching it on the event loop.

A unit test against ``schema.execute`` would miss both, and getting the second
one wrong locks every logged-in console user out of the API.
"""

import json

import pytest
from django.test import AsyncClient

from karakter.models import User

MANAGEMENT_URL = "/lok/managementgraphql/"


def _post(client, query, variables=None):
    return client.post(
        MANAGEMENT_URL,
        data=json.dumps({"query": query, "variables": variables or {}}),
        content_type="application/json",
    )


async def _json(response):
    return json.loads(response.content.decode())


@pytest.fixture
def console_user(db):
    """A user with a password, so the test client can log in over a session."""
    # Creating the user fires the signal that builds their personal organization,
    # so no explicit Organization is needed (and creating one would collide).
    return User.objects.create_user(username="console", password="hunter2hunter2")


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_authenticated_session_can_query_management_api(console_user):
    """A logged-in console user must reach resolvers.

    This is the regression test for the auth gate reading ``request.user`` on the
    event loop: if it does, resolving the lazy user raises
    ``SynchronousOnlyOperation``, the gate treats that as "anonymous", and the
    whole console breaks for authenticated users.
    """
    client = AsyncClient()
    assert await client.alogin(username="console", password="hunter2hunter2")

    response = await _post(client, "{ me { id username } }")
    payload = await _json(response)

    assert "errors" not in payload, payload
    assert payload["data"]["me"]["username"] == "console"


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_anonymous_post_is_rejected(db):
    """Anonymous callers must not reach any non-public root field."""
    client = AsyncClient()

    response = await _post(client, "{ me { id } }")
    payload = await _json(response)

    assert payload.get("data") in (None, {"me": None})
    assert payload["errors"], payload
    assert "Authentication required" in payload["errors"][0]["message"]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_anonymous_cannot_read_single_objects(db):
    """The IDOR surface (single-object lookups by id) must deny anonymous reads."""
    client = AsyncClient()

    for query in (
        '{ organization(id: "1") { id } }',
        '{ hub(id: "1") { id } }',
        '{ ionscaleAuthKey(id: "1") { id } }',
        '{ socialAccount(id: "1") { id } }',
    ):
        payload = await _json(await _post(client, query))
        assert payload["errors"], f"{query} returned data anonymously: {payload}"
        assert "Authentication required" in payload["errors"][0]["message"], payload


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_get_queries_are_disabled(db):
    """GET is CSRF-exempt, so queries over GET must not be served at all."""
    client = AsyncClient()
    response = await client.get(MANAGEMENT_URL, {"query": "{ __typename }"})
    assert response.status_code in (400, 405), response.status_code


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_anonymous_can_still_preview_a_public_invite(db):
    """`inviteByCode` is the pre-login preview the SPA hits from an invite link.

    `ManagementInvite` is now scoped by `get_queryset` to an organization's owner
    and admins, because it exposes bearer tokens. This asserts that scoping did not
    close the one invite flow whose caller is, by design, not a member of the
    organization at all — it resolves a model instance directly, so the type-level
    queryset filter must not apply.
    """
    from asgiref.sync import sync_to_async

    from karakter.models import Invite, Organization

    def _setup():
        owner = User.objects.create_user(username="inviteowner", password="hunter2hunter2")
        org = Organization.objects.create(slug="previeworg", name="Preview Org", owner=owner)
        return Invite.objects.create(created_by=owner, created_for=org, public=True)

    invite = await sync_to_async(_setup)()

    payload = await _json(
        await _post(
            AsyncClient(),
            "query ($code: String!) { inviteByCode(inviteCode: $code) { id public } }",
            {"code": str(invite.token)},
        )
    )

    assert "errors" not in payload, payload
    assert payload["data"]["inviteByCode"]["public"] is True
