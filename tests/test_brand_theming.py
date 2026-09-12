"""Brand hue/chroma: the values a fakts client themes itself from.

The tint a client paints is `membership.brand_* -> organization.brand_* -> the
client's own default`, so both levels have to be readable off the **main**
(non-management) schema — that is the only one a fakts client is issued a token
for. Both schemas carry a write path: `setMembershipBrandHue` (management, which
names its organization) and `updateMembershipColors` (main, which acts on the
organization the token is scoped to).
"""

import pytest
from asgiref.sync import sync_to_async

from api.management.schema import schema as management_schema
from karakter.models import Membership
from lok_server.schema import schema as main_schema

ME_QUERY = """
query {
  me {
    memberships {
      brandHue
      brandChroma
      organization { brandHue brandChroma }
    }
  }
}
"""


async def _scoped_membership(context) -> Membership:
    """The membership the request is scoped to.

    The fixture user also owns a signup org, so `me` returns more than one
    membership — this is the one whose organization the token names.
    """
    return await Membership.objects.select_related("organization").aget(
        user=context.request.user, organization=context.request.organization
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_main_schema_exposes_membership_and_organization_brand(db, authenticated_context):
    membership = await _scoped_membership(authenticated_context)
    membership.brand_hue, membership.brand_chroma = 210.0, 0.14
    await sync_to_async(membership.save)()

    organization = membership.organization
    organization.brand_hue, organization.brand_chroma = 267.0, 0.19
    await sync_to_async(organization.save)()

    result = await main_schema.execute(ME_QUERY, context_value=authenticated_context)
    assert not result.errors, result.errors

    got = next(
        m for m in result.data["me"]["memberships"] if m["brandHue"] == 210.0
    )
    assert got["brandChroma"] == 0.14
    assert got["organization"] == {"brandHue": 267.0, "brandChroma": 0.19}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_unset_membership_brand_reads_as_null(db, authenticated_context):
    """Null is meaningful: it is what tells a client to fall back to the org
    default rather than to paint 0."""
    membership = await _scoped_membership(authenticated_context)
    assert membership.brand_hue is None and membership.brand_chroma is None

    result = await main_schema.execute(ME_QUERY, context_value=authenticated_context)
    assert not result.errors, result.errors
    for m in result.data["me"]["memberships"]:
        assert m["brandHue"] is None
        assert m["brandChroma"] is None


SET_BRAND = """
mutation ($input: SetMembershipBrandHueInput!) {
  setMembershipBrandHue(input: $input) { id brandHue brandChroma }
}
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_setting_one_brand_field_leaves_the_other_alone(db, authenticated_context):
    """The reason the input uses UNSET rather than a null default.

    `brandHue` and `brandChroma` share one mutation, so if an omitted field read
    as null, kontrol's existing hue picker — which sends only `brandHue` — would
    silently wipe the member's chroma on every hue change.
    """
    organization = authenticated_context.request.organization

    async def _set(**fields):
        result = await management_schema.execute(
            SET_BRAND,
            context_value=authenticated_context,
            variable_values={"input": {"organization": str(organization.pk), **fields}},
        )
        assert not result.errors, result.errors
        return result.data["setMembershipBrandHue"]

    both = await _set(brandHue=210.0, brandChroma=0.14)
    assert (both["brandHue"], both["brandChroma"]) == (210.0, 0.14)

    # Hue-only write (what the current picker sends): chroma survives.
    assert (await _set(brandHue=42.0))["brandChroma"] == 0.14

    # An *explicit* null still clears — that is how "use the org default" is said.
    assert (await _set(brandChroma=None))["brandChroma"] is None
    assert (await _set())["brandHue"] == 42.0


UPDATE_COLORS = """
mutation ($input: UpdateMembershipColorsInput!) {
  updateMembershipColors(input: $input) { id brandHue brandChroma }
}
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_main_schema_updates_the_callers_own_colors(db, authenticated_context):
    """The main-schema write path, with the same UNSET semantics as its twin.

    A client holding only a main-schema token has no organization argument to
    pass — the row it writes is the one its token already names.
    """

    async def _set(**fields):
        result = await main_schema.execute(
            UPDATE_COLORS,
            context_value=authenticated_context,
            variable_values={"input": fields},
        )
        assert not result.errors, result.errors
        return result.data["updateMembershipColors"]

    both = await _set(brandHue=210.0, brandChroma=0.14)
    assert (both["brandHue"], both["brandChroma"]) == (210.0, 0.14)

    # Hue-only write: chroma survives, because an omitted field is UNSET.
    assert (await _set(brandHue=42.0))["brandChroma"] == 0.14

    # An *explicit* null still clears — that is how "use the org default" is said.
    assert (await _set(brandChroma=None))["brandChroma"] is None
    assert (await _set())["brandHue"] == 42.0

    membership = await _scoped_membership(authenticated_context)
    assert (membership.brand_hue, membership.brand_chroma) == (42.0, None)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_main_schema_colors_touch_only_the_callers_membership(db, authenticated_context):
    """The row is looked up by `(caller, active organization)`, so one member's
    colours can never land on another tenant's — or another member's — row."""
    from tests import factories
    from tests.conftest import build_auth_context

    def _other_principal():
        other = factories.make_membership()
        return build_auth_context(other.user, other.organization, factories.make_client(membership=other)), other

    other_context, other = await sync_to_async(_other_principal)()

    result = await main_schema.execute(
        UPDATE_COLORS,
        context_value=authenticated_context,
        variable_values={"input": {"brandHue": 12.0, "brandChroma": 0.2}},
    )
    assert not result.errors, result.errors

    await other.arefresh_from_db()
    assert other.brand_hue is None and other.brand_chroma is None

    mine = await _scoped_membership(authenticated_context)
    assert mine.pk != other.pk
    assert (mine.brand_hue, mine.brand_chroma) == (12.0, 0.2)
