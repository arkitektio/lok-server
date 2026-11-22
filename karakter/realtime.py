"""Broadcasts behind the ``myMessages`` and ``membershipRequests`` subscriptions.

Signal receivers, so every write path is covered, and on commit, so a
subscriber never re-fetches a row that is not there yet (or was rolled back).
The hub health broadcast lives with the report it follows
(``fakts.services.hubs.report_hub_health``).
"""

from typing import Any

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from karakter.channels import (
    MembershipRequestSignal,
    SystemMessageSignal,
    membership_request_channel,
    membership_requests_group,
    system_message_channel,
    user_messages_group,
)
from karakter.models import MembershipRequest, SystemMessage


@receiver(post_save, sender=SystemMessage, dispatch_uid="lok_realtime_system_message_saved")
def system_message_saved(sender: type[SystemMessage], instance: SystemMessage, created: bool, **kwargs: Any) -> None:
    signal = SystemMessageSignal(create=instance.pk) if created else SystemMessageSignal(update=instance.pk)
    system_message_channel.broadcast_on_commit(signal, [user_messages_group(instance.user_id)])


@receiver(post_delete, sender=SystemMessage, dispatch_uid="lok_realtime_system_message_deleted")
def system_message_deleted(sender: type[SystemMessage], instance: SystemMessage, **kwargs: Any) -> None:
    system_message_channel.broadcast_on_commit(SystemMessageSignal(delete=instance.pk), [user_messages_group(instance.user_id)])


@receiver(post_save, sender=MembershipRequest, dispatch_uid="lok_realtime_membership_request_saved")
def membership_request_saved(sender: type[MembershipRequest], instance: MembershipRequest, created: bool, **kwargs: Any) -> None:
    signal = MembershipRequestSignal(create=instance.pk) if created else MembershipRequestSignal(update=instance.pk)
    membership_request_channel.broadcast_on_commit(signal, [membership_requests_group(instance.organization_id)])
