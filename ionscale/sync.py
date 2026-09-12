"""Keep ionscale in step with lok's memberships.

Everything here is *best effort and never raises*: the control plane being
down must not fail a membership change in lok. Failures are logged; the
``reconcile_meshes`` management command repairs whatever was missed.

Two things happen when a membership goes away:

1. ``revoke_member`` — ``RevokeAccount`` deletes the user's machines and keys
   in the organization's tailnet and pushes the change to peers right away.
   The IAM ``subs`` list alone only takes effect at the *next login*; without
   revocation a removed member's machines would stay connected until their
   node keys expire.
2. ``resync_organization`` — rewrites ``subs`` so the identity cannot log in
   again.

Callers inside a transaction should go through ``schedule_*`` so ionscale only
learns about committed state.
"""

import logging
from typing import Optional

from django.db import transaction

logger = logging.getLogger(__name__)


def _configured() -> bool:
    from .manager import ionscale_configured

    return ionscale_configured()


def revoke_member(user_pk, organization_pk: Optional[object] = None) -> None:
    """Revoke an identity's mesh access in one organization (or, with
    ``organization_pk=None``, in every tailnet). Never raises."""
    if not _configured():
        return
    from .repo import get_ionscale_repo

    org = str(organization_pk) if organization_pk is not None else None
    try:
        affected = get_ionscale_repo().revoke_account(str(user_pk), org)
    except Exception:
        logger.exception(
            "Could not revoke ionscale access for user %s in organization %s; "
            "run `manage.py reconcile_meshes --revoke-orphans` to repair",
            user_pk,
            org or "<all>",
        )
        return
    if affected:
        logger.info("Revoked ionscale access for user %s in tailnets %s", user_pk, affected)


def resync_organization(organization_pk) -> None:
    """Push the organization's current member list to each of its meshes.
    Never raises."""
    if not _configured():
        return
    from fakts.models import IonscaleLayer
    from .manager import sync

    for layer in IonscaleLayer.objects.filter(organization_id=organization_pk):
        try:
            sync(layer)
        except Exception:
            logger.exception(
                "Could not sync members of organization %s to mesh %s; "
                "run `manage.py reconcile_meshes` to repair",
                organization_pk,
                layer.tailnet_name,
            )


def teardown_tailnet(tailnet_name: str) -> None:
    """Delete a tailnet (and all its machines) after its layer is gone. A tailnet
    that no longer exists counts as torn down. Never raises."""
    if not _configured():
        return
    from .errors import IonscaleError
    from .repo import get_ionscale_repo

    try:
        get_ionscale_repo().delete_tailnet(tailnet_name, force=True)
    except IonscaleError as exc:
        if exc.code == "not_found":
            return
        logger.exception("Could not delete tailnet %s; delete it manually", tailnet_name)
    except Exception:
        logger.exception("Could not delete tailnet %s; delete it manually", tailnet_name)


def schedule_resync(organization_pk) -> None:
    if not _configured():
        return
    transaction.on_commit(lambda: resync_organization(organization_pk))


def schedule_member_removal(user_pk, organization_pk) -> None:
    """After commit: revoke the user's access, then rewrite ``subs``. Skipped when
    the organization itself is gone — its tailnet is torn down as a whole."""
    if not _configured():
        return

    def _run() -> None:
        from karakter.models import Organization

        if not Organization.objects.filter(pk=organization_pk).exists():
            return
        revoke_member(user_pk, organization_pk)
        resync_organization(organization_pk)

    transaction.on_commit(_run)


def schedule_user_revocation(user_pk) -> None:
    """After commit: revoke the user everywhere (deactivation)."""
    if not _configured():
        return
    transaction.on_commit(lambda: revoke_member(user_pk, None))


def schedule_teardown(tailnet_name: str) -> None:
    if not _configured():
        return
    transaction.on_commit(lambda: teardown_tailnet(tailnet_name))
