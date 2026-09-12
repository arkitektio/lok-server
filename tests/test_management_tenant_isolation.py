"""Cross-tenant isolation regressions for the management API.

The endpoint-wide gate in ``RequireAuthenticationExtension`` only stops anonymous
callers. These tests cover the second half of the problem: an *authenticated*
member of org A must not be able to read or mutate org B's objects by guessing
sequential ids.

Denials are asserted on the shared "Not found, or you are not authorized" text so
the error cannot be used as an existence oracle.

Setup runs through ``sync_to_async`` (matching ``test_alias_public``) because the
ORM is not usable from the event loop the async schema executes on.
"""

import pytest
from asgiref.sync import sync_to_async

from api.management.authz import HUB_ADMIN_REQUIRED
from api.management.schema import schema as management_schema
from fakts import models as fakts_models
from tests import factories
from tests.conftest import build_auth_context


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
    assert "not authorized" in message or "Authentication required" in message, message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_cannot_read_other_orgs_organization():
    context, _org_a, org_b, _attacker, _victim = await sync_to_async(_two_org_setup)()

    result = await management_schema.execute(
        "query ($id: ID!) { organization(id: $id) { id slug } }",
        context_value=context,
        variable_values={"id": str(org_b.id)},
    )
    _assert_denied(result)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_cannot_read_other_orgs_hub():
    context, _org_a, org_b, _attacker, _victim = await sync_to_async(_two_org_setup)()
    hub = await sync_to_async(factories.make_hub)(organization=org_b)

    result = await management_schema.execute(
        "query ($id: ID!) { hub(id: $id) { id name } }",
        context_value=context,
        variable_values={"id": str(hub.id)},
    )
    _assert_denied(result)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_can_read_own_orgs_hub():
    """The scoping must not be so tight that legitimate access breaks."""
    context, org_a, _org_b, _attacker, _victim = await sync_to_async(_two_org_setup)()
    hub = await sync_to_async(factories.make_hub)(organization=org_a)

    result = await management_schema.execute(
        "query ($id: ID!) { hub(id: $id) { id name } }",
        context_value=context,
        variable_values={"id": str(hub.id)},
    )
    assert not result.errors, result.errors
    assert result.data["hub"]["id"] == str(hub.id)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_cannot_take_over_another_organization():
    """``changeOrganizationOwner`` was the full-takeover mutation."""
    context, _org_a, org_b, attacker, victim = await sync_to_async(_two_org_setup)()

    result = await management_schema.execute(
        "mutation ($org: ID!, $new: ID!) { changeOrganizationOwner(input: {organization: $org, newOwner: $new}) { id } }",
        context_value=context,
        variable_values={"org": str(org_b.id), "new": str(attacker.id)},
    )
    assert result.errors, f"takeover succeeded: {result.data}"

    owner_id = await sync_to_async(
        lambda: fakts_models.Organization.objects.get(id=org_b.id).owner_id
    )()
    assert owner_id == victim.id


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_cannot_delete_another_orgs_device():
    context, _org_a, org_b, _attacker, _victim = await sync_to_async(_two_org_setup)()
    device = await sync_to_async(fakts_models.Device.objects.create)(
        organization=org_b, node_id="victim-node", name="victim"
    )

    result = await management_schema.execute(
        "mutation ($id: ID!) { deleteDevice(input: {id: $id}) }",
        context_value=context,
        variable_values={"id": str(device.id)},
    )
    _assert_denied(result)

    still_there = await sync_to_async(fakts_models.Device.objects.filter(id=device.id).exists)()
    assert still_there


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_cannot_hijack_another_orgs_alias():
    """Aliases are routing entries: repointing one hijacks that service's traffic."""

    def setup():
        context, _org_a, org_b, _attacker, _victim = _two_org_setup()
        hub = factories.make_hub(organization=org_b)
        instance = factories.make_service_instance(hub=hub)
        alias = fakts_models.InstanceAlias.objects.create(
            instance=instance, host="victim.example", port=443, kind="absolute"
        )
        return context, alias

    context, alias = await sync_to_async(setup)()

    result = await management_schema.execute(
        """
        mutation ($id: ID!) {
            updateAlias(input: {id: $id, host: "attacker.example", port: 8080, kind: "absolute"}) { id host }
        }
        """,
        context_value=context,
        variable_values={"id": str(alias.id)},
    )
    _assert_denied(result)

    host = await sync_to_async(
        lambda: fakts_models.InstanceAlias.objects.get(id=alias.id).host
    )()
    assert host == "victim.example"


@pytest.mark.django_db(transaction=True)
def test_upload_key_is_namespaced_to_the_caller():
    """An attacker-chosen upload key must not address another tenant's object."""
    from api.management.mutations.upload import _scoped_key

    context, _org_a, _org_b, attacker, _victim = _two_org_setup()
    info = type("Info", (), {"context": context})()

    assert _scoped_key(info, "avatar.png") == f"users/{attacker.id}/avatar.png"
    # Traversal and absolute paths cannot escape the per-user prefix.
    assert _scoped_key(info, "../../victim/avatar.png") == f"users/{attacker.id}/avatar.png"
    assert _scoped_key(info, "/etc/passwd") == f"users/{attacker.id}/passwd"


def _invite_leak_setup():
    """An org with a pending admin invite, plus a plain (non-admin) member of it.

    The member is deliberately given no roles: they are the least-privileged
    principal who can still traverse `organization { invites }`.
    """
    from karakter.models import Invite, Role

    owner = factories.make_user()
    org = factories.make_organization(owner=owner)

    guest_membership = factories.make_membership(organization=org)
    guest = guest_membership.user
    guest_membership.roles.clear()

    invite = Invite.objects.create(created_by=owner, created_for=org)
    invite.roles.add(Role.objects.get(organization=org, identifier="admin"))

    request_client = factories.make_client(membership=guest_membership)
    guest_context = build_auth_context(guest, org, request_client)

    owner_membership = org.memberships.get(user=owner)
    owner_client = factories.make_client(membership=owner_membership)
    owner_context = build_auth_context(owner, org, owner_client)

    return guest_context, owner_context, org, invite


INVITES_QUERY = "query ($id: ID!) { organization(id: $id) { invites { id token inviteUrl } } }"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_plain_member_cannot_read_their_orgs_invite_tokens():
    """An invite token is a bearer credential.

    `ManagementInvite` had no `get_queryset`, so any member — including one with no
    roles at all — could reach it through `organization { invites }` and read the
    tokens of pending invites, then redeem one granting `admin`.
    """
    guest_context, _owner_context, org, _invite = await sync_to_async(_invite_leak_setup)()

    result = await management_schema.execute(
        INVITES_QUERY, context_value=guest_context, variable_values={"id": str(org.id)}
    )

    assert not result.errors, result.errors
    assert result.data["organization"]["invites"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_owner_can_still_read_their_orgs_invites():
    """The scoping must not be so tight that legitimate invite management breaks."""
    _guest_context, owner_context, org, invite = await sync_to_async(_invite_leak_setup)()

    result = await management_schema.execute(
        INVITES_QUERY, context_value=owner_context, variable_values={"id": str(org.id)}
    )

    assert not result.errors, result.errors
    tokens = [row["token"] for row in result.data["organization"]["invites"]]
    assert tokens == [str(invite.token)]



def _org_with_admin_and_member():
    """An org with an owner, an admin, and a plain member — each with a context."""
    from karakter.models import Role

    owner_membership = factories.make_membership()
    owner = owner_membership.user
    org = owner_membership.organization
    org.owner = owner
    org.save()

    admin_membership = factories.make_membership(organization=org)
    admin_membership.roles.add(Role.objects.get(organization=org, identifier="admin"))

    member_membership = factories.make_membership(organization=org)
    member_membership.roles.clear()

    def ctx(membership):
        client = factories.make_client(membership=membership)
        return build_auth_context(membership.user, org, client)

    return {
        "org": org,
        "owner": ctx(org.memberships.get(user=owner)),
        "admin": ctx(admin_membership),
        "member": ctx(member_membership),
        "owner_membership": org.memberships.get(user=owner),
        "admin_membership": admin_membership,
        "member_membership": member_membership,
    }


DELETE_MEMBERSHIP = 'mutation ($id: ID!) { deleteMembership(input: {id: $id}) }'


async def _delete(context, membership):
    return await management_schema.execute(
        DELETE_MEMBERSHIP, context_value=context, variable_values={"id": str(membership.id)}
    )


async def _exists(membership):
    from karakter.models import Membership

    return await sync_to_async(Membership.objects.filter(pk=membership.pk).exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_admin_can_remove_a_member():
    """Previously self-only, so an organization had no way to eject anyone."""
    s = await sync_to_async(_org_with_admin_and_member)()

    result = await _delete(s["admin"], s["member_membership"])

    assert not result.errors, result.errors
    assert not await _exists(s["member_membership"])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_owner_can_remove_a_member():
    s = await sync_to_async(_org_with_admin_and_member)()

    result = await _delete(s["owner"], s["member_membership"])

    assert not result.errors, result.errors
    assert not await _exists(s["member_membership"])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_member_can_still_leave():
    s = await sync_to_async(_org_with_admin_and_member)()

    result = await _delete(s["member"], s["member_membership"])

    assert not result.errors, result.errors
    assert not await _exists(s["member_membership"])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_plain_member_cannot_remove_someone_else():
    s = await sync_to_async(_org_with_admin_and_member)()

    result = await _delete(s["member"], s["admin_membership"])

    # Not the uniform DENIED text: the caller is already a member of this
    # organization, so a specific message leaks nothing they don't know.
    assert result.errors, f"removal succeeded: {result.data}"
    assert "not allowed to manage memberships" in result.errors[0].message
    assert await _exists(s["admin_membership"])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_admin_cannot_remove_the_owner():
    """Ejecting the owner would leave the org owned by a non-member, and is a step
    toward taking it over."""
    s = await sync_to_async(_org_with_admin_and_member)()

    result = await _delete(s["admin"], s["owner_membership"])

    assert result.errors, f"owner was removable: {result.data}"
    assert "owner cannot be removed" in result.errors[0].message
    assert await _exists(s["owner_membership"])


# --------------------------------------------------------------------------- #
# adding a hub is an owner/admin operation
# --------------------------------------------------------------------------- #


ACCEPT_HUB = "mutation ($input: AcceptHubDeviceCodeInput!) { acceptHubDeviceCode(input: $input) { id name } }"


def _staged_hub_code(identifier: str):
    return factories.make_device_code(
        kind="hub",
        staging_manifest={"identifier": identifier, "instances": [], "clients": []},
    )


async def _accept(context, device_code, organization):
    return await management_schema.execute(
        ACCEPT_HUB,
        context_value=context,
        variable_values={
            "input": {
                "deviceCode": str(device_code.id),
                "code": device_code.code,
                "organization": str(organization.id),
                "allowIonscale": False,
            }
        },
    )


async def _hub_count(organization, identifier):
    return await sync_to_async(
        lambda: fakts_models.Hub.objects.filter(organization=organization, identifier=identifier).count()
    )()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_plain_member_cannot_add_a_hub():
    """A hub provisions instances, roles, scopes and clients into the tenant, so a
    plain member is told to ask an admin rather than doing it themselves."""
    s = await sync_to_async(_org_with_admin_and_member)()
    device_code = await sync_to_async(_staged_hub_code)("member-hub")

    result = await _accept(s["member"], device_code, s["org"])

    assert result.errors, f"hub was created by a plain member: {result.data}"
    assert result.errors[0].message == HUB_ADMIN_REQUIRED
    assert await _hub_count(s["org"], "member-hub") == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_admin_can_add_a_hub():
    s = await sync_to_async(_org_with_admin_and_member)()
    device_code = await sync_to_async(_staged_hub_code)("admin-hub")

    result = await _accept(s["admin"], device_code, s["org"])

    assert not result.errors, result.errors
    assert result.data["acceptHubDeviceCode"]["name"] == "admin-hub"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_owner_can_add_a_hub():
    s = await sync_to_async(_org_with_admin_and_member)()
    device_code = await sync_to_async(_staged_hub_code)("owner-hub")

    result = await _accept(s["owner"], device_code, s["org"])

    assert not result.errors, result.errors
    assert result.data["acceptHubDeviceCode"]["name"] == "owner-hub"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_non_member_adding_a_hub_gets_the_uniform_denial():
    """The friendly "ask an admin" sentence must not double as an existence
    oracle: someone outside the tenant still only learns DENIED."""
    context, _org_a, org_b, _attacker, _victim = await sync_to_async(_two_org_setup)()
    device_code = await sync_to_async(_staged_hub_code)("outsider-hub")

    result = await _accept(context, device_code, org_b)

    _assert_denied(result)
    assert result.errors[0].message != HUB_ADMIN_REQUIRED
    assert await _hub_count(org_b, "outsider-hub") == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_am_i_admin_reflects_the_same_bar():
    s = await sync_to_async(_org_with_admin_and_member)()

    async def am_i_admin(context):
        result = await management_schema.execute(
            "query ($id: ID!) { organization(id: $id) { amIAdmin amIOwner } }",
            context_value=context,
            variable_values={"id": str(s["org"].id)},
        )
        assert not result.errors, result.errors
        return result.data["organization"]

    assert (await am_i_admin(s["owner"])) == {"amIAdmin": True, "amIOwner": True}
    assert (await am_i_admin(s["admin"])) == {"amIAdmin": True, "amIOwner": False}
    assert (await am_i_admin(s["member"])) == {"amIAdmin": False, "amIOwner": False}
