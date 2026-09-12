import logging

from kante.types import Info
from django.contrib.auth import get_user_model

from fakts import inputs, models, types
from karakter.authz import get_scoped_or_denied


logger = logging.getLogger(__name__)

User = get_user_model()


def update_device(info: Info, input: inputs.UpdateDeviceInput) -> types.Device:
    """Rename a device, scoped to the caller's organization.

    The management twin (`api.management.mutations.device.update_device`) already
    asserted membership; this copy fetched by bare pk.
    """
    node = get_scoped_or_denied(models.Device.objects, info, id=input.id)

    if input.name:
        node.name = input.name

    node.save()
    return node
