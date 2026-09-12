"""Alias persistence helpers.

A single place to turn a wire-level ``StagingAlias`` into a persisted
``InstanceAlias`` row, so the hub-manifest path and the device-code
hub path stay in agreement (they used to diverge on which fields they
wrote and on the upsert key).
"""

from fakts import base_models, models


def upsert_instance_alias(
    instance: models.ServiceInstance,
    alias: base_models.StagingAlias,
) -> tuple[models.InstanceAlias, bool]:
    """Create or update the ``InstanceAlias`` for a staging alias.

    The natural key is the model's uniqueness identity
    ``(instance, host, port, ssl, path, kind)`` so the upsert agrees with the
    DB ``UniqueConstraint``. The remaining descriptive fields
    (``name``, ``scope``, ``public``) are written as defaults.

    ``challenge`` is only written when the staging alias provides one; otherwise
    it is left to the model default. The column is NOT NULL, so passing an
    explicit ``None`` (the ``StagingAlias`` default) would violate the constraint.
    """
    ssl = alias.ssl if alias.ssl is not None else True

    defaults = {
        "name": alias.name or alias.id,
        "scope": alias.scope,
        "public": alias.public,
    }
    if alias.challenge is not None:
        defaults["challenge"] = alias.challenge

    return models.InstanceAlias.objects.update_or_create(
        instance=instance,
        host=alias.host,
        port=alias.port,
        ssl=ssl,
        path=alias.path,
        kind=alias.kind,
        defaults=defaults,
    )
