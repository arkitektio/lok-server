"""``Organization.deeplink_apps`` — the apps kontrol's ``/deeplink`` and
``/smartlink`` pages may forward an organization's members to — and the
management-side ``requestMembership`` those pages offer to an outsider.

The app list is an owner setting, readable by every member (the link pages need
it). Its protocols are held to plain app schemes and its install links to https:
anything else would turn those pages into an open redirect.
"""

import pytest
from asgiref.sync import sync_to_async

from api.management.schema import schema as management_schema
from karakter import models
from karakter.deeplinks import MAX_APPS, default_deeplink_apps, normalize_deeplink_apps
from karakter.graphql.mutations import membership_request as request_module
from tests import factories
from tests.conftest import build_auth_context

UPDATE_ORGANIZATION = """
    mutation ($input: UpdateOrganizationInput!) {
        updateOrganization(input: $input) { id deeplinkApps { protocol name installUrl mobile } }
    }
"""

READ_ORGANIZATION = """
    query ($id: ID!) {
        organization(id: $id) { id deeplinkApps { protocol name installUrl mobile } }
    }
"""

REQUEST_MEMBERSHIP = """
    mutation ($input: RequestMembershipInput!) { requestMembership(input: $input) }
"""

ORKESTRATOR = {
    "protocol": "orkestrator",
    "name": "Orkestrator",
    "installUrl": "https://arkitekt.live/docs/use/install",
    "mobile": True,
}


# --------------------------------------------------------------------------- #
# the rules
# --------------------------------------------------------------------------- #


def test_the_default_is_orkestrator_with_its_install_page_on_desktop_and_mobile():
    assert default_deeplink_apps() == [
        {
            "protocol": "orkestrator",
            "name": "Orkestrator",
            "install_url": "https://arkitekt.live/docs/use/install",
            "mobile": True,
        }
    ]
    # The default has to satisfy the rules it is the default for.
    assert normalize_deeplink_apps(default_deeplink_apps()) == default_deeplink_apps()


def test_normalize_fills_in_defaults_and_keeps_the_order():
    assert normalize_deeplink_apps(
        [
            {"protocol": " My-App:// ", "install_url": "example.com/get", "mobile": True},
            {"protocol": "Orkestrator", "name": "  The Desktop App "},
        ]
    ) == [
        {"protocol": "my-app", "name": "My-app", "install_url": "https://example.com/get", "mobile": True},
        {"protocol": "orkestrator", "name": "The Desktop App", "install_url": None, "mobile": False},
    ]


def test_normalize_accepts_an_empty_list():
    assert normalize_deeplink_apps([]) == []


@pytest.mark.parametrize("protocol", ["https", "HTTP", "javascript", "data", "file", "mailto", "javascript:"])
def test_normalize_refuses_browser_schemes(protocol):
    with pytest.raises(ValueError, match="cannot be used"):
        normalize_deeplink_apps([{"protocol": protocol}])


@pytest.mark.parametrize("protocol", ["", " ", "1app", "my app", "app/evil", "app://host", "ap_p", "a" * 33])
def test_normalize_refuses_malformed_schemes(protocol):
    with pytest.raises(ValueError, match="not a valid protocol"):
        normalize_deeplink_apps([{"protocol": protocol}])


@pytest.mark.parametrize(
    "install_url",
    ["http://example.com", "javascript://alert(1)", "data://text/html,x", "https://", "https://exa mple.com", "ftp://example.com"],
)
def test_normalize_refuses_install_links_that_are_not_https(install_url):
    with pytest.raises(ValueError, match="not a valid install link"):
        normalize_deeplink_apps([{"protocol": "app", "install_url": install_url}])


def test_normalize_refuses_duplicates_and_caps_the_list():
    with pytest.raises(ValueError, match="more than once"):
        normalize_deeplink_apps([{"protocol": "app"}, {"protocol": "APP://"}])
    assert len(normalize_deeplink_apps([{"protocol": f"app{i}"} for i in range(MAX_APPS)])) == MAX_APPS
    with pytest.raises(ValueError, match="at most"):
        normalize_deeplink_apps([{"protocol": f"app{i}"} for i in range(MAX_APPS + 1)])


# --------------------------------------------------------------------------- #
# the management API
# --------------------------------------------------------------------------- #


def _setup():
    """An organization, its owner's context, and a plain member's context."""
    organization = factories.make_organization()
    owner_membership = factories.make_membership(user=organization.owner, organization=organization)
    owner_context = build_auth_context(
        organization.owner, organization, factories.make_client(membership=owner_membership)
    )
    member = factories.make_membership(organization=organization)
    member_context = build_auth_context(
        member.user, organization, factories.make_client(membership=member), roles=()
    )
    return organization, owner_context, member_context


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_new_organization_allows_orkestrator():
    organization, owner_context, _ = await sync_to_async(_setup)()

    result = await management_schema.execute(
        READ_ORGANIZATION, variable_values={"id": str(organization.id)}, context_value=owner_context
    )
    assert not result.errors, result.errors
    assert result.data["organization"]["deeplinkApps"] == [ORKESTRATOR]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_owner_registers_apps_and_members_can_read_them():
    organization, owner_context, member_context = await sync_to_async(_setup)()

    result = await management_schema.execute(
        UPDATE_ORGANIZATION,
        variable_values={
            "input": {
                "id": str(organization.id),
                "deeplinkApps": [
                    {"protocol": "My-App", "installUrl": "example.com/get"},
                    {"protocol": "orkestrator://", "name": "Orkestrator", "mobile": True},
                ],
            }
        },
        context_value=owner_context,
    )
    assert not result.errors, result.errors
    expected = [
        {"protocol": "my-app", "name": "My-app", "installUrl": "https://example.com/get", "mobile": False},
        {"protocol": "orkestrator", "name": "Orkestrator", "installUrl": None, "mobile": True},
    ]
    assert result.data["updateOrganization"]["deeplinkApps"] == expected

    # The link pages run as whichever member opened the link.
    result = await management_schema.execute(
        READ_ORGANIZATION, variable_values={"id": str(organization.id)}, context_value=member_context
    )
    assert not result.errors, result.errors
    assert result.data["organization"]["deeplinkApps"] == expected


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_owner_can_switch_forwarding_off_and_other_updates_leave_the_list_alone():
    organization, owner_context, _ = await sync_to_async(_setup)()

    result = await management_schema.execute(
        UPDATE_ORGANIZATION,
        variable_values={"input": {"id": str(organization.id), "deeplinkApps": []}},
        context_value=owner_context,
    )
    assert not result.errors, result.errors
    assert result.data["updateOrganization"]["deeplinkApps"] == []

    # Omitting the field means "unchanged", not "reset".
    result = await management_schema.execute(
        UPDATE_ORGANIZATION,
        variable_values={"input": {"id": str(organization.id), "description": "still off"}},
        context_value=owner_context,
    )
    assert not result.errors, result.errors
    assert result.data["updateOrganization"]["deeplinkApps"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "apps",
    [
        [{"protocol": "https"}],
        [{"protocol": "orkestrator"}, {"protocol": "javascript"}],
        [{"protocol": "not a scheme"}],
        [{"protocol": "app", "installUrl": "javascript://alert(1)"}],
    ],
)
async def test_bad_apps_are_refused_and_nothing_is_stored(apps):
    organization, owner_context, _ = await sync_to_async(_setup)()

    result = await management_schema.execute(
        UPDATE_ORGANIZATION,
        variable_values={"input": {"id": str(organization.id), "deeplinkApps": apps}},
        context_value=owner_context,
    )
    assert result.errors

    stored = await sync_to_async(models.Organization.objects.get)(pk=organization.pk)
    assert stored.deeplink_apps == default_deeplink_apps()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_plain_member_cannot_change_the_apps():
    organization, _, member_context = await sync_to_async(_setup)()

    result = await management_schema.execute(
        UPDATE_ORGANIZATION,
        variable_values={"input": {"id": str(organization.id), "deeplinkApps": [{"protocol": "evil-app"}]}},
        context_value=member_context,
    )
    assert result.errors

    stored = await sync_to_async(models.Organization.objects.get)(pk=organization.pk)
    assert stored.deeplink_apps == default_deeplink_apps()


# --------------------------------------------------------------------------- #
# asking to join, by handle
# --------------------------------------------------------------------------- #


@pytest.fixture
def quiet_notifications(monkeypatch):
    """No pushes from tests; record who would have been told instead."""
    sent = []
    monkeypatch.setattr(request_module, "_notify", lambda recipients, title, message: sent.append(list(recipients)))
    return sent


def _outsider_setup():
    """An outsider signed into their own organization, and a target with a handle."""
    outsider = factories.make_membership()
    target = factories.make_organization()
    target.slug = "target-lab"
    target.save(update_fields=["slug"])
    factories.make_membership(user=target.owner, organization=target)
    context = build_auth_context(
        outsider.user, outsider.organization, factories.make_client(membership=outsider)
    )
    return outsider, target, context


def _requests(**lookup):
    return [(r.user_id, r.status, r.reason) for r in models.MembershipRequest.objects.filter(**lookup)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_outsider_asks_to_join_by_handle(quiet_notifications):
    outsider, target, context = await sync_to_async(_outsider_setup)()

    result = await management_schema.execute(
        REQUEST_MEMBERSHIP,
        variable_values={"input": {"organization": "Target-Lab", "reason": "I was sent a link"}},
        context_value=context,
    )
    assert not result.errors, result.errors
    assert result.data == {"requestMembership": True}

    assert await sync_to_async(_requests)(organization=target) == [
        (outsider.user_id, "pending", "I was sent a link")
    ]
    # The owner is told.
    assert len(quiet_notifications) == 1 and len(quiet_notifications[0]) == 1

    # Asking twice stores nothing new and answers the same.
    result = await management_schema.execute(
        REQUEST_MEMBERSHIP, variable_values={"input": {"organization": "target-lab"}}, context_value=context
    )
    assert result.data == {"requestMembership": True}
    assert len(await sync_to_async(_requests)(organization=target)) == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_unknown_handle_answers_the_same_and_stores_nothing(quiet_notifications):
    _, _, context = await sync_to_async(_outsider_setup)()

    result = await management_schema.execute(
        REQUEST_MEMBERSHIP, variable_values={"input": {"organization": "no-such-org"}}, context_value=context
    )
    assert not result.errors, result.errors
    assert result.data == {"requestMembership": True}
    assert await sync_to_async(_requests)() == []
    assert quiet_notifications == []


# --------------------------------------------------------------------------- #
# answering a request, from kontrol's dashboard
# --------------------------------------------------------------------------- #

PENDING = """
    query ($id: ID!) { organization(id: $id) { membershipRequests { id reason status user { id username } } } }
"""

APPROVE = """
    mutation ($input: ApproveMembershipRequestInput!) {
        approveMembershipRequest(input: $input) { id user { id } roles { identifier } }
    }
"""

DECLINE = """
    mutation ($input: DeclineMembershipRequestInput!) { declineMembershipRequest(input: $input) { id status } }
"""


def _inbox_setup():
    """A target organization with an owner, an admin and a plain member, and two
    outsiders who asked to join."""
    from karakter.managers import create_role

    target = factories.make_organization()
    create_role(organization=target, identifier="guest")
    owner = factories.make_membership(user=target.owner, organization=target)
    admin = factories.make_membership(organization=target)
    admin.roles.add(create_role(organization=target, identifier="admin"))
    member = factories.make_membership(organization=target)

    def context(membership, roles=("admin",)):
        return build_auth_context(
            membership.user, target, factories.make_client(membership=membership), roles=roles
        )

    first = models.MembershipRequest.objects.create(user=factories.make_user(), organization=target, reason="hi")
    second = models.MembershipRequest.objects.create(user=factories.make_user(), organization=target)
    return {
        "target": target,
        "owner": context(owner),
        "admin": context(admin),
        "member": context(member, roles=()),
        "first": first,
        "second": second,
    }


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_owner_and_admin_see_pending_requests_and_a_plain_member_sees_none(quiet_notifications):
    s = await sync_to_async(_inbox_setup)()
    variables = {"id": str(s["target"].id)}

    for who in ("owner", "admin"):
        result = await management_schema.execute(PENDING, variable_values=variables, context_value=s[who])
        assert not result.errors, result.errors
        listed = result.data["organization"]["membershipRequests"]
        assert [r["id"] for r in listed] == [str(s["first"].id), str(s["second"].id)]
        assert listed[0]["reason"] == "hi" and listed[0]["user"]["id"] == str(s["first"].user_id)

    result = await management_schema.execute(PENDING, variable_values=variables, context_value=s["member"])
    assert not result.errors, result.errors
    assert result.data["organization"]["membershipRequests"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_admin_approves_one_request_and_declines_the_other(quiet_notifications):
    s = await sync_to_async(_inbox_setup)()

    approved = await management_schema.execute(
        APPROVE, variable_values={"input": {"id": str(s["first"].id)}}, context_value=s["admin"]
    )
    assert not approved.errors, approved.errors
    assert approved.data["approveMembershipRequest"]["user"]["id"] == str(s["first"].user_id)
    assert [r["identifier"] for r in approved.data["approveMembershipRequest"]["roles"]] == ["guest"]
    # The requester is told.
    assert len(quiet_notifications) == 1

    declined = await management_schema.execute(
        DECLINE, variable_values={"input": {"id": str(s["second"].id)}}, context_value=s["admin"]
    )
    assert not declined.errors, declined.errors
    assert declined.data["declineMembershipRequest"]["status"] == "declined"

    def state():
        return (
            models.Membership.objects.filter(user_id=s["first"].user_id, organization=s["target"]).exists(),
            models.Membership.objects.filter(user_id=s["second"].user_id, organization=s["target"]).exists(),
        )

    assert await sync_to_async(state)() == (True, False)

    # Nothing is left pending, and an answered request cannot be answered again.
    result = await management_schema.execute(
        PENDING, variable_values={"id": str(s["target"].id)}, context_value=s["owner"]
    )
    assert result.data["organization"]["membershipRequests"] == []
    again = await management_schema.execute(
        APPROVE, variable_values={"input": {"id": str(s["second"].id)}}, context_value=s["admin"]
    )
    assert again.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_plain_member_and_an_outsider_cannot_answer_a_request(quiet_notifications):
    s = await sync_to_async(_inbox_setup)()

    def outsider_context():
        outsider = factories.make_membership()
        return build_auth_context(
            outsider.user, outsider.organization, factories.make_client(membership=outsider)
        )

    for context in (s["member"], await sync_to_async(outsider_context)()):
        for document in (APPROVE, DECLINE):
            result = await management_schema.execute(
                document, variable_values={"input": {"id": str(s["first"].id)}}, context_value=context
            )
            assert result.errors

    def still_pending():
        return models.MembershipRequest.objects.get(pk=s["first"].pk).status

    assert await sync_to_async(still_pending)() == "pending"
