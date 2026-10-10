"""What kontrol's link pages may show to someone who merely holds a link.

A link (`/deeplink/<org>/...`, `/smartlink/<org>/...`, optionally with
`?user_id=<id>` naming who shared it) is opened by people who are not members
of the organization, often not even signed in. By default they learn nothing
from it. Two opt-ins change that:

* ``Organization.public_link_preview`` — the organization's name, description
  and logo may be shown, so the visitor sees where the link leads.
* ``Profile.public_link_preview`` — the sharer's name and picture may be shown.

The sharer is only ever shown for an organization that opted in too, and only
if they really are a member of it: otherwise a link could claim anyone as its
sender, or reveal who belongs to an organization that wants to stay unseen.
Everything that is not opted in looks exactly like something that does not exist.
"""

from karakter import models


def build_link_preview(organization_slug: str, user_id=None):
    # Imported here: the management types import the models' app registry.
    from api.management import types

    organization = (
        models.Organization.objects.filter(slug=(organization_slug or "").strip().lower(), public_link_preview=True)
        .select_related("profile__avatar")
        .first()
    )
    if organization is None:
        return types.ManagementLinkPreview(organization=None, inviter=None)

    profile = getattr(organization, "profile", None)
    preview = types.ManagementLinkPreviewOrganization(
        slug=organization.slug,
        name=(profile.name if profile else None) or organization.name,
        description=organization.description,
        avatar=profile.avatar if profile else None,
    )

    inviter = None
    try:
        user_pk = int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        user_pk = None
    if user_pk is not None:
        sharer = (
            models.Profile.objects.filter(
                user_id=user_pk,
                public_link_preview=True,
                user__memberships__organization=organization,
            )
            .select_related("user", "avatar")
            .first()
        )
        if sharer is not None:
            inviter = types.ManagementLinkPreviewUser(
                name=sharer.name or sharer.user.username, avatar=sharer.avatar
            )

    return types.ManagementLinkPreview(organization=preview, inviter=inviter)
