import logging
from typing import TYPE_CHECKING, Any

from allauth.account.signals import user_signed_up
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db.models import Q
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver
from django.http import HttpRequest

from karakter import managers

from .models import Membership, Organization, OrganizationProfile, Profile

if TYPE_CHECKING:
    from fakts.models import AppMeshEnrollment, Hub
    from fakts.models import Client as FaktsClient

    from .models import User as UserModel

logger = logging.getLogger(__name__)
User = get_user_model()


@receiver(post_save, sender=Organization)
def ensure_default_roles_for_org(
    sender: type[Organization],
    instance: Organization,
    created: bool,
    **kwargs: Any,
) -> None:
    managers.create_default_roles_for_org(instance)
    managers.ensure_owner_is_admin(instance)
    managers.create_default_scopes_for_org(instance)
    if created:
        OrganizationProfile.objects.create(organization=instance)
        # Every organization has a mesh by default — including the personal one a
        # signup creates, which no mutation ever touches. On commit and never
        # raising, like the other mesh side effects below.
        from ionscale.sync import schedule_mesh_provisioning

        schedule_mesh_provisioning(instance.pk)


@receiver(post_save, sender=User)
def create_or_update_user_profile(
    sender: "type[UserModel]",
    instance: "UserModel",
    created: bool,
    **kwargs: Any,
) -> None:
    if created:
        Profile.objects.create(user=instance)
        instance.profile.save()

        for i in settings.SYSTEM_MESSAGES:
            instance.notify(i["title"], i["message"])


@receiver(post_save, sender=User)
def create_user_default_organization(
    sender: "type[UserModel]",
    instance: "UserModel",
    created: bool,
    **kwargs: Any,
) -> None:
    if created:
        managers.create_user_default_organization(instance)


@receiver(user_signed_up)
def user_signed_up_handler(
    request: HttpRequest, user: "UserModel", **kwargs: Any
) -> None:
    # Automatically sets the user to inactive until approved
    user.save()


@receiver(post_save, sender=User)
def notify_user_activation(
    sender: "type[UserModel]",
    instance: "UserModel",
    created: bool,
    **kwargs: Any,
) -> None:
    if not created and instance.is_active:
        # Only send if previously inactive
        try:
            send_mail(
                "Your account has been approved",
                "You can now log in.",
                "no-reply@example.com",
                [instance.email],
            )
        except Exception:
            logger.error("Failed to send activation email", exc_info=True)
            # Handle the error as needed, e.g., log it or notify admins


# Mesh (ionscale) side effects. All of them run on commit and never raise, so
# an unreachable control plane cannot fail a membership change here; see
# ionscale/sync.py and the `reconcile_meshes` command that repairs misses.


@receiver(post_save, sender=Membership)
def sync_ionscale_layers_on_membership_save(
    sender: type[Membership],
    instance: Membership,
    **kwargs: Any,
) -> None:
    from ionscale.sync import schedule_resync

    schedule_resync(instance.organization_id)


@receiver(post_delete, sender=Membership)
def revoke_ionscale_access_on_membership_delete(
    sender: type[Membership],
    instance: Membership,
    **kwargs: Any,
) -> None:
    from ionscale.sync import schedule_member_removal

    # Capture the keys now: by commit time the instance's relations may be gone
    # (user or organization cascade).
    schedule_member_removal(instance.user_id, instance.organization_id)


@receiver(pre_save, sender=User)
def revoke_ionscale_access_on_deactivation(
    sender: "type[UserModel]",
    instance: "UserModel",
    **kwargs: Any,
) -> None:
    if instance.pk is None or instance.is_active:
        return
    was_active = (
        User.objects.filter(pk=instance.pk).values_list("is_active", flat=True).first()
    )
    if was_active:
        from ionscale.sync import schedule_user_revocation

        schedule_user_revocation(instance.pk)


@receiver(pre_delete, sender="fakts.AppMeshEnrollment")
def revoke_ionscale_nodes_on_app_enrollment_delete(
    sender: "type[AppMeshEnrollment]",
    instance: "AppMeshEnrollment",
    **kwargs: Any,
) -> None:
    # Key-joined app nodes are owned by the tailnet's service user, not the
    # member, so account revocation misses them. Also fires when a membership
    # (or user) delete cascades the enrollment away.
    from ionscale.sync import schedule_enrollment_revocation

    schedule_enrollment_revocation(instance)


@receiver(pre_delete, sender="fakts.Hub")
def revoke_ionscale_nodes_on_hub_delete(
    sender: "type[Hub]",
    instance: "Hub",
    **kwargs: Any,
) -> None:
    # A hub's nodes joined with a minted key, like an app's; its own tag
    # (`tag:hub-<pk>`) finds them. Also fires on an organization cascade, where
    # the tailnet teardown wins (schedule_enrollment_revocation checks).
    from ionscale.sync import schedule_enrollment_revocation

    schedule_enrollment_revocation(instance)


@receiver(post_delete, sender="fakts.AppMeshEnrollment")
def reapply_acl_on_app_enrollment_delete(
    sender: "type[AppMeshEnrollment]",
    instance: "AppMeshEnrollment",
    **kwargs: Any,
) -> None:
    from ionscale.acl import schedule_acl_apply

    schedule_acl_apply(instance.organization_id)


@receiver(post_delete, sender="fakts.Hub")
def delete_hub_identity_with_the_hub(
    sender: "type[Hub]",
    instance: "Hub",
    **kwargs: Any,
) -> None:
    # The hub server's credential dies with the hub (its refresh chain included);
    # left alone it would keep a membership-bound session for a hub that is gone.
    from fakts.models import Client
    from ionscale.acl import schedule_acl_apply

    if instance.client_id:
        Client.objects.filter(pk=instance.client_id).delete()
    schedule_acl_apply(instance.organization_id)


@receiver(pre_delete, sender="fakts.Client")
def reap_mesh_sidecar_on_client_delete(
    sender: "type[FaktsClient]",
    instance: "FaktsClient",
    **kwargs: Any,
) -> None:
    # A sidecar lives only while a live client backs it. The check runs at
    # commit, so a re-authorization (old client deleted, new one bound in the
    # same transaction) keeps its sidecar.
    from fakts.models import Hub
    from fakts.services.mesh import schedule_client_reap
    from ionscale.acl import schedule_acl_apply

    schedule_client_reap(
        membership_id=instance.membership_id,
        app_id=instance.release.app_id if instance.release_id else None,
        device_id=instance.node_id,
        hub_id=Hub.objects.filter(client=instance).values_list("pk", flat=True).first(),
    )
    schedule_acl_apply(instance.organization_id)


@receiver(pre_delete, sender=Organization)
def teardown_ionscale_meshes_for_organization(
    sender: type[Organization],
    instance: Organization,
    **kwargs: Any,
) -> None:
    from fakts.models import IonscaleLayer
    from ionscale.sync import schedule_teardown

    for tailnet_name in IonscaleLayer.objects.filter(organization=instance).values_list(
        "tailnet_name", flat=True
    ):
        schedule_teardown(tailnet_name)


@receiver(pre_delete, sender=Organization)
def delete_oauth_clients_for_organization(
    sender: type[Organization],
    instance: Organization,
    **kwargs: Any,
) -> None:
    from fakts.models import Client

    # The unified Client cascades from both its membership and organization
    # FKs; this catches rows bound only one way (defensive).
    Client.objects.filter(
        Q(membership__organization=instance) | Q(organization=instance)
    ).distinct().delete()
