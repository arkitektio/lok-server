"""Shared proof-of-possession fetch for the device-code mutations.

All device-code mutations (``accept_*`` and ``decline_*``) used to take a device
code by primary key with no check beyond "is authenticated". Primary keys are
sequential, so any authenticated account could walk them and accept or deny
every pending device, hub or mesh enrolment in the deployment.

The legitimate caller always holds the *code* — it is what the device displayed
and what the configure URL carries (``/configure/{code}``), and the SPA looks the
id up from it via ``deviceCodeByCode``. Requiring the code as proof turns an
enumerable id into an unguessable capability.

``code`` is required: an id-only call was accepted (with a warning) while the
deployed SPA still sent only the id; that fallback is gone, which is what
actually closes the enumeration.
"""

import logging

from graphql import GraphQLError

from api.management.authz import DENIED, get_or_denied

logger = logging.getLogger(__name__)


def resolve_device_code_with_proof(model, *, device_code_id, code, **extra_lookup):
    """Fetch a device code, requiring proof-of-possession of its user code.

    ``code`` must match the stored code, so a caller who guessed an id cannot
    accept or deny someone else's enrolment. ``extra_lookup`` narrows the fetch
    (e.g. ``kind="hub"``). A miss, a malformed id and a wrong code all raise the
    same :data:`DENIED` error so none of them is an existence oracle.
    """
    instance = get_or_denied(model.objects, id=device_code_id, **extra_lookup)

    if code is None or instance.code != code:
        raise GraphQLError(DENIED)

    return instance


# Backwards-compatible alias: the decline mutations were the first callers.
resolve_declinable_device_code = resolve_device_code_with_proof
