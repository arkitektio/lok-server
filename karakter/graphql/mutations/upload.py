from pathlib import PurePosixPath

from kante.types import Info
import strawberry

from fakts import types, models
from karakter.authz import get_user
from karakter.datalayer import get_current_datalayer
from django.conf import settings

# A presigned POST grants write access to exactly one key, so the key must not be
# attacker-chosen. Mirrors `api.management.mutations.upload`, whose docstring
# records the same hazard. `MEDIA_BUCKET` is served publicly by the gateway, so an
# unnamespaced key also meant arbitrary content hosted on the deployment's domain.
UPLOAD_MAX_BYTES = 10 * 1024 * 1024
UPLOAD_EXPIRES_IN = 3600


@strawberry.input()
class RequestMediaUploadInput:
    key: str
    datalayer: str


def _scoped_key(info: Info, key: str) -> str:
    """Namespace an attacker-controlled upload key under the calling user."""
    user = get_user(info)
    # Strip directories and traversal so the prefix cannot be escaped.
    safe = PurePosixPath(key).name or "upload"
    return f"users/{user.id}/{safe}"


def request_media_upload(
    info: Info, input: RequestMediaUploadInput
) -> types.PresignedPostCredentials:
    """Request upload credentials for a given key"""

    datalayer = get_current_datalayer()
    key = _scoped_key(info, input.key)

    response = datalayer.s3v4.generate_presigned_post(
        Bucket=settings.MEDIA_BUCKET,
        Key=key,
        Fields=None,
        # Bound what the presign authorizes: without a content-length condition
        # the holder can upload an object of any size.
        Conditions=[["content-length-range", 0, UPLOAD_MAX_BYTES]],
        ExpiresIn=UPLOAD_EXPIRES_IN,
    )

    path = f"s3://{settings.MEDIA_BUCKET}/{key}"

    store, _ = models.MediaStore.objects.get_or_create(
        path=path, key=key, bucket=settings.MEDIA_BUCKET
    )

    aws = {
        "key": response["fields"]["key"],
        "x_amz_algorithm": response["fields"]["x-amz-algorithm"],
        "x_amz_credential": response["fields"]["x-amz-credential"],
        "x_amz_date": response["fields"]["x-amz-date"],
        "x_amz_signature": response["fields"]["x-amz-signature"],
        "policy": response["fields"]["policy"],
        "bucket": settings.MEDIA_BUCKET,
        "datalayer": input.datalayer,
        "store": store.id,
    }

    return types.PresignedPostCredentials(**aws)
