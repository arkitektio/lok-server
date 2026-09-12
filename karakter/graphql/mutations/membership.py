import logging

import strawberry
from kante.types import Info

from karakter import models, types
from karakter.authz import get_or_denied, get_organization, get_user

logger = logging.getLogger(__name__)


@strawberry.input
class UpdateMembershipColorsInput:
    # UNSET (field omitted) leaves the stored value alone; an explicit null
    # clears it back to the organization default. The two have to be tellable
    # apart because hue and chroma share this mutation — otherwise a client
    # sending only `brandHue` would silently wipe the member's chroma.
    brand_hue: float | None = strawberry.UNSET
    brand_chroma: float | None = strawberry.UNSET


def update_membership_colors(info: Info, input: UpdateMembershipColorsInput) -> types.Membership:
    """Set the caller's personal brand hue/chroma for their active organization.

    The main-schema twin of `setMembershipBrandHue` on the management API, for
    the clients that only ever hold a token for this endpoint. No organization
    argument: this schema only ever addresses the organization named by the
    token's `org` claim (the same one `me { memberships }` is narrowed to), and
    the row is looked up by `(caller, active organization)` — so a member can
    only recolour their own view of the organization they are scoped to.

    Pass an explicit null `brandHue`/`brandChroma` to clear it (falling back to
    the organization default); omit a field to leave it unchanged.
    """
    membership = get_or_denied(
        models.Membership.objects,
        user=get_user(info),
        organization=get_organization(info),
    )

    updated = []
    if input.brand_hue is not strawberry.UNSET:
        membership.brand_hue = input.brand_hue
        updated.append("brand_hue")
    if input.brand_chroma is not strawberry.UNSET:
        membership.brand_chroma = input.brand_chroma
        updated.append("brand_chroma")

    if updated:
        membership.save(update_fields=updated)
        logger.info("Updated brand colours (%s) on membership %s", ", ".join(updated), membership.pk)
    return membership
