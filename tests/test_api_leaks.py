"""Information-leak and capability regressions for the management API.

Covers the device-code capability model (by-id access is tenant-scoped, accepting
requires proof of possession of the user code), the secret-bearing fields that
are now gated or gone (hub claim token, redeem token, mesh auth key, invite
e-mail, personal user details), and the tenant scoping of the service/app
catalog.

Setup runs through ``sync_to_async`` (matching ``test_management_tenant_isolation``)
because the ORM is not usable from the event loop the async schema executes on.
"""

import pytest
from asgiref.sync import sync_to_async

from api.management.authz import DENIED
from api.management.schema import schema as management_schema
from fakts import models as fakts_models
from fakts.enums import ClientKindChoices
from karakter import models as karakter_models
from kante.context import HttpContext, TemporalResponse, UniversalRequest
from tests import factories
from tests.conftest import build_auth_context


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _owner_context(organization):
    """An authenticated context for the organization's owner (who is also admin)."""
    membership = factories.make_membership(user=organization.owner, organization=organization)
    request_client = factories.make_client(membership=membership)
    return build_auth_context(organization.owner, organization, request_client)


def _member_context(organization, user=None):
    """An authenticated context for a plain (role-less) member of ``organization``."""
    membership = factories.make_membership(user=user, organization=organization)
    request_client = factories.make_client(membership=membership)
    return build_auth_context(membership.user, organization, request_client)


def _anonymous_context():
    request = UniversalRequest(_extensions={})
    return HttpContext(request=request, response=TemporalResponse(), headers={}, type="http")


def _two_org_setup():
    """Two unrelated tenants plus an authenticated context for the attacker's org."""
    attacker_membership = factories.make_membership()
    request_client = factories.make_client(membership=attacker_membership)
    attacker = attacker_membership.user
    org_a = attacker_membership.organization

    victim = factories.make_user()
    org_b = factories.make_organization(owner=victim)

    context = build_auth_context(attacker, org_a, request_client)
    return context, org_a, org_b, attacker, victim


def _assert_denied(result):
    assert result.errors, f"expected a denial, got data: {result.data}"
    message = result.errors[0].message
    assert message == DENIED or "not authorized" in message, message


def _sdl_block(type_name: str) -> str:
    sdl = management_schema.as_str()
    start = sdl.index(f"type {type_name} ")
    return sdl[start : sdl.index("\n}", start)]


# --------------------------------------------------------------------------- #
# A. device-code capability model
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_device_code_by_id_is_tenant_scoped():
    """``deviceCode(id)`` used to be a bare ``objects.get`` — any authenticated user
    could read any tenant's (pending or accepted) device code, including its user
    code, by walking sequential ids."""
    context, org_a, org_b, _attacker, _victim = await sync_to_async(_two_org_setup)()

    def _codes():
        theirs = factories.make_device_code(organization=org_b)
        mine = factories.make_device_code(organization=org_a)
        pending = factories.make_device_code()  # organization is NULL while pending
        return theirs, mine, pending

    theirs, mine, pending = await sync_to_async(_codes)()

    query = "query ($id: ID!) { deviceCode(id: $id) { id code } }"

    result = await management_schema.execute(query, context_value=context, variable_values={"id": str(theirs.id)})
    _assert_denied(result)

    # A pending code has no organization yet: it is reachable only through the
    # capability path (`deviceCodeByCode`), never by id.
    result = await management_schema.execute(query, context_value=context, variable_values={"id": str(pending.id)})
    _assert_denied(result)

    result = await management_schema.execute(query, context_value=context, variable_values={"id": str(mine.id)})
    assert not result.errors, result.errors
    assert result.data["deviceCode"]["id"] == str(mine.id)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_hub_device_code_by_id_is_tenant_scoped():
    context, _org_a, org_b, _attacker, _victim = await sync_to_async(_two_org_setup)()
    theirs = await sync_to_async(factories.make_device_code)(
        organization=org_b,
        kind="hub",
        staging_manifest={"identifier": "theirhub", "instances": [], "clients": []},
    )

    result = await management_schema.execute(
        "query ($id: ID!) { hubDeviceCode(id: $id) { id code } }",
        context_value=context,
        variable_values={"id": str(theirs.id)},
    )
    _assert_denied(result)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_mesh_device_code_by_id_is_tenant_scoped():
    """A mesh device code is tied to a tenant through the key minted on accept
    (``auth_key -> layer -> organization``). Another tenant must not see it by id;
    a pending one (no key, no user) is invisible by id to everyone."""
    from ionscale.manager import ensure_org_mesh

    context, _org_a, org_b, _attacker, victim = await sync_to_async(_two_org_setup)()

    def _setup():
        layer = ensure_org_mesh(org_b)
        key = fakts_models.IonscaleAuthKey.objects.create(layer=layer, key="tskey-secret", creator=victim)
        from django.utils import timezone
        import datetime

        accepted = fakts_models.MeshDeviceCode.objects.create(
            code="mesh-accepted",
            challenge_code="mesh-accepted-challenge",
            auth_key=key,
            expires_at=timezone.now() + datetime.timedelta(minutes=5),
        )
        pending = fakts_models.MeshDeviceCode.objects.create(
            code="mesh-pending",
            challenge_code="mesh-pending-challenge",
            expires_at=timezone.now() + datetime.timedelta(minutes=5),
        )
        victim_context = _owner_context(org_b)
        return accepted, pending, victim_context

    accepted, pending, victim_context = await sync_to_async(_setup)()
    query = "query ($id: ID!) { meshDeviceCode(id: $id) { id code } }"

    _assert_denied(await management_schema.execute(query, context_value=context, variable_values={"id": str(accepted.id)}))
    _assert_denied(await management_schema.execute(query, context_value=context, variable_values={"id": str(pending.id)}))

    # The tenant that accepted it can still read it by id.
    result = await management_schema.execute(query, context_value=victim_context, variable_values={"id": str(accepted.id)})
    assert not result.errors, result.errors
    assert result.data["meshDeviceCode"]["id"] == str(accepted.id)


ACCEPT_DEVICE_CODE = """
    mutation ($input: AcceptDeviceCodeInput!) {
        acceptDeviceCode(input: $input) { id }
    }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_accept_device_code_requires_proof_of_possession():
    """Accepting without the user code is a schema error, with a wrong code is a
    denial, with the right code succeeds (for a member of the hub's organization)."""

    def _setup():
        org = factories.make_organization()
        context = _member_context(org)
        hub = factories.make_hub(organization=org)
        device_code = factories.make_device_code()
        return context, hub, device_code

    context, hub, device_code = await sync_to_async(_setup)()

    # `code` is required on the input type.
    result = await management_schema.execute(
        ACCEPT_DEVICE_CODE,
        context_value=context,
        variable_values={"input": {"deviceCode": str(device_code.id), "hub": str(hub.id)}},
    )
    assert result.errors, "accepting without a code must be rejected"
    assert "code" in result.errors[0].message

    result = await management_schema.execute(
        ACCEPT_DEVICE_CODE,
        context_value=context,
        variable_values={"input": {"deviceCode": str(device_code.id), "hub": str(hub.id), "code": "wrong-code"}},
    )
    _assert_denied(result)
    still_unbound = await sync_to_async(lambda: fakts_models.DeviceCode.objects.get(pk=device_code.pk).client.membership_id)()
    assert still_unbound is None

    result = await management_schema.execute(
        ACCEPT_DEVICE_CODE,
        context_value=context,
        variable_values={"input": {"deviceCode": str(device_code.id), "hub": str(hub.id), "code": device_code.code}},
    )
    assert not result.errors, result.errors
    assert result.data["acceptDeviceCode"]["id"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_accept_device_code_into_another_tenants_hub_is_denied():
    context, _org_a, org_b, _attacker, _victim = await sync_to_async(_two_org_setup)()

    def _setup():
        hub = factories.make_hub(organization=org_b)
        device_code = factories.make_device_code()
        return hub, device_code

    hub, device_code = await sync_to_async(_setup)()
    result = await management_schema.execute(
        ACCEPT_DEVICE_CODE,
        context_value=context,
        variable_values={"input": {"deviceCode": str(device_code.id), "hub": str(hub.id), "code": device_code.code}},
    )
    _assert_denied(result)


VALIDATE_DEVICE_CODE = """
    query ($dc: ID!, $hub: ID!, $code: String!) {
        validateDeviceCode(deviceCode: $dc, hub: $hub, code: $code) { valid reason }
    }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_validate_device_code_cross_tenant_is_denied():
    """``validateDeviceCode`` took any hub id and any device code id and ran the
    requirement matching for them — reading another tenant's pending manifest."""
    context, org_a, org_b, _attacker, _victim = await sync_to_async(_two_org_setup)()

    def _setup():
        their_hub = factories.make_hub(organization=org_b)
        my_hub = factories.make_hub(organization=org_a)
        device_code = factories.make_device_code()
        return their_hub, my_hub, device_code

    their_hub, my_hub, device_code = await sync_to_async(_setup)()

    result = await management_schema.execute(
        VALIDATE_DEVICE_CODE,
        context_value=context,
        variable_values={"dc": str(device_code.id), "hub": str(their_hub.id), "code": device_code.code},
    )
    _assert_denied(result)

    # Own hub but a guessed id without the code -> denied too.
    result = await management_schema.execute(
        VALIDATE_DEVICE_CODE,
        context_value=context,
        variable_values={"dc": str(device_code.id), "hub": str(my_hub.id), "code": "nope"},
    )
    _assert_denied(result)

    # Own hub + the displayed code -> works.
    result = await management_schema.execute(
        VALIDATE_DEVICE_CODE,
        context_value=context,
        variable_values={"dc": str(device_code.id), "hub": str(my_hub.id), "code": device_code.code},
    )
    assert not result.errors, result.errors
    assert result.data["validateDeviceCode"]["valid"] is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_hub_device_code_hub_is_null_for_non_members():
    """The by-code lookup is the capability path, but the hub it was accepted into
    belongs to a tenant: only that tenant's members get to see it."""
    context, _org_a, org_b, _attacker, _victim = await sync_to_async(_two_org_setup)()

    def _setup():
        dc = factories.make_device_code(
            organization=org_b,
            kind="hub",
            staging_manifest={"identifier": "theirhub", "instances": [], "clients": []},
        )
        hub = factories.make_hub(organization=org_b, client=dc.client)
        return dc, hub, _owner_context(org_b)

    dc, hub, victim_context = await sync_to_async(_setup)()
    query = "query ($code: String!) { hubDeviceCodeByCode(code: $code) { id hub { id } } }"

    result = await management_schema.execute(query, context_value=context, variable_values={"code": dc.code})
    assert not result.errors, result.errors
    assert result.data["hubDeviceCodeByCode"]["hub"] is None

    result = await management_schema.execute(query, context_value=victim_context, variable_values={"code": dc.code})
    assert not result.errors, result.errors
    assert result.data["hubDeviceCodeByCode"]["hub"]["id"] == str(hub.id)


# --------------------------------------------------------------------------- #
# B. secret-bearing fields
# --------------------------------------------------------------------------- #


def test_secret_and_raw_fields_are_gone_from_the_schema():
    """Fields that carried credentials or raw identifiers are removed outright."""
    assert "token" not in _sdl_block("ManagementHub")
    assert "nodeId" not in _sdl_block("ManagementStagingManifest")
    assert "redirectUris" not in _sdl_block("ManagementOAuth2Client")
    partner = _sdl_block("ManagementKommunityPartner")
    assert "preconfiguredHub" not in partner
    assert "filterConfig" not in partner
    assert "managementLayers" not in _sdl_block("Query")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_invite_email_is_hidden_from_anonymous_preview_but_visible_to_owner():
    def _setup():
        org = factories.make_organization()
        invite = karakter_models.Invite.objects.create(
            created_by=org.owner, created_for=org, public=True, email="invitee@example.com"
        )
        return invite, _owner_context(org)

    invite, owner_context = await sync_to_async(_setup)()

    result = await management_schema.execute(
        "query ($code: String!) { inviteByCode(inviteCode: $code) { id email createdBy { email firstName } } }",
        context_value=_anonymous_context(),
        variable_values={"code": str(invite.token)},
    )
    assert not result.errors, result.errors
    assert result.data["inviteByCode"]["email"] is None
    # And the inviter's personal details are hidden from the anonymous visitor too.
    assert result.data["inviteByCode"]["createdBy"]["email"] is None

    result = await management_schema.execute(
        "query ($id: ID!) { invite(id: $id) { id email } }",
        context_value=owner_context,
        variable_values={"id": str(invite.id)},
    )
    assert not result.errors, result.errors
    assert result.data["invite"]["email"] == "invitee@example.com"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_redeem_token_secret_only_for_creator_or_owner_admin():
    def _setup():
        org = factories.make_organization()
        hub = factories.make_hub(organization=org)
        token = factories.make_redeem_token(hub=hub, user=org.owner)
        return token, _owner_context(org), _member_context(org)

    token, owner_context, member_context = await sync_to_async(_setup)()
    query = "{ redeemTokens { id token } }"

    result = await management_schema.execute(query, context_value=owner_context)
    assert not result.errors, result.errors
    assert result.data["redeemTokens"][0]["token"] == token.token

    # A plain member of the same organization can list it but not read the secret.
    result = await management_schema.execute(query, context_value=member_context)
    assert not result.errors, result.errors
    assert result.data["redeemTokens"][0]["id"] == str(token.id)
    assert result.data["redeemTokens"][0]["token"] is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_redeem_token_is_owner_admin_only():
    def _setup():
        org = factories.make_organization()
        hub = factories.make_hub(organization=org)
        return hub, _owner_context(org), _member_context(org)

    hub, owner_context, guest_context = await sync_to_async(_setup)()
    mutation = "mutation ($hub: ID!) { createRedeemToken(input: {hub: $hub}) { id token } }"

    _assert_denied(await management_schema.execute(mutation, context_value=guest_context, variable_values={"hub": str(hub.id)}))

    result = await management_schema.execute(mutation, context_value=owner_context, variable_values={"hub": str(hub.id)})
    assert not result.errors, result.errors
    # The creator sees the secret they just minted.
    assert result.data["createRedeemToken"]["token"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_ionscale_auth_key_secret_only_for_creator_or_owner_admin():
    from ionscale.manager import ensure_org_mesh

    def _setup():
        org = factories.make_organization()
        layer = ensure_org_mesh(org)
        key = fakts_models.IonscaleAuthKey.objects.create(layer=layer, key="tskey-very-secret", creator=org.owner)
        return key, _owner_context(org), _member_context(org)

    key, owner_context, member_context = await sync_to_async(_setup)()
    query = "{ ionscaleAuthKeys { id key } }"

    result = await management_schema.execute(query, context_value=owner_context)
    assert not result.errors, result.errors
    assert result.data["ionscaleAuthKeys"][0]["key"] == "tskey-very-secret"

    result = await management_schema.execute(query, context_value=member_context)
    assert not result.errors, result.errors
    assert result.data["ionscaleAuthKeys"][0]["key"] is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_user_personal_details_only_for_self_and_colleagues():
    """`email`/`firstName`/`lastName` follow the `friends` scoping; `socialAccounts`
    is self-only."""

    def _setup():
        org = factories.make_organization()
        me = factories.make_user(first_name="Me", last_name="Myself")
        colleague = factories.make_user(first_name="Col", last_name="League")
        factories.make_membership(user=colleague, organization=org)
        membership = factories.make_membership(user=me, organization=org)
        request_client = factories.make_client(membership=membership)
        return build_auth_context(me, org, request_client), me, colleague

    context, me, colleague = await sync_to_async(_setup)()

    result = await management_schema.execute(
        "{ me { id email firstName socialAccounts { id } } friends { id email firstName socialAccounts { id } } }",
        context_value=context,
    )
    assert not result.errors, result.errors
    assert result.data["me"]["email"] == me.email
    assert result.data["me"]["firstName"] == "Me"
    by_id = {row["id"]: row for row in result.data["friends"]}
    assert by_id[str(colleague.id)]["email"] == colleague.email
    assert by_id[str(colleague.id)]["firstName"] == "Col"
    assert by_id[str(colleague.id)]["socialAccounts"] == []


# --------------------------------------------------------------------------- #
# C. tenant scoping of the catalog
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_catalog_lists_exclude_other_tenants():
    """``services``/``serviceInstances``/``apps`` (and friends) were global lists."""
    context, org_a, org_b, _attacker, _victim = await sync_to_async(_two_org_setup)()

    def _setup():
        mine_service = factories.make_service(organization=org_a)
        theirs_service = factories.make_service(organization=org_b)
        my_hub = factories.make_hub(organization=org_a)
        their_hub = factories.make_hub(organization=org_b)
        mine_instance = factories.make_service_instance(hub=my_hub, release=factories.make_service_release(service=mine_service))
        theirs_instance = factories.make_service_instance(hub=their_hub, release=factories.make_service_release(service=theirs_service))
        mine_app = factories.make_app(organization=org_a)
        theirs_app = factories.make_app(organization=org_b)
        return mine_service, theirs_service, mine_instance, theirs_instance, mine_app, theirs_app

    mine_service, theirs_service, mine_instance, theirs_instance, mine_app, theirs_app = await sync_to_async(_setup)()

    result = await management_schema.execute(
        "{ services { id } serviceInstances { id } apps { id } serviceReleases { id } }",
        context_value=context,
    )
    assert not result.errors, result.errors
    service_ids = {row["id"] for row in result.data["services"]}
    instance_ids = {row["id"] for row in result.data["serviceInstances"]}
    app_ids = {row["id"] for row in result.data["apps"]}
    assert str(mine_service.id) in service_ids and str(theirs_service.id) not in service_ids
    assert str(mine_instance.id) in instance_ids and str(theirs_instance.id) not in instance_ids
    assert str(mine_app.id) in app_ids and str(theirs_app.id) not in app_ids
    assert len(result.data["serviceReleases"]) == 1

    # And the single-object roots are scoped the same way.
    for field, obj in (("service", theirs_service), ("serviceInstance", theirs_instance), ("app", theirs_app)):
        result = await management_schema.execute(
            f"query ($id: ID!) {{ {field}(id: $id) {{ id }} }}",
            context_value=context,
            variable_values={"id": str(obj.id)},
        )
        _assert_denied(result)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_oauth2_client_by_client_id_is_not_an_existence_oracle():
    context, *_ = await sync_to_async(_two_org_setup)()
    result = await management_schema.execute(
        'query { oauth2ClientByClientId(clientId: "does-not-exist") { id } }',
        context_value=context,
    )
    _assert_denied(result)
