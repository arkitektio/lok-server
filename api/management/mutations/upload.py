from pathlib import PurePosixPath

from kante.types import Info
import strawberry

from fakts import types, models
from api.management.authz import get_user
from api.management.datalayer import get_current_datalayer
from django.conf import settings


@strawberry.input()
class RequestMediaUploadInput:
    key: str
    datalayer: str


def _scoped_key(info: Info, key: str) -> str:
    """Namespace an attacker-controlled upload key under the calling user.

    The presigned POST grants write access to exactly one key in MEDIA_BUCKET, so
    an unnamespaced `key` let any authenticated caller overwrite any other
    tenant's media object (and made two users uploading "avatar.png" collide).
    Callers only ever use the returned MediaStore id afterwards, so rewriting the
    key here is transparent to them.
    """
    user = get_user(info)
    # Strip path traversal and absolute paths so the prefix cannot be escaped.
    safe = PurePosixPath(key).name or "upload"
    return f"users/{user.id}/{safe}"


def request_media_upload(info: Info, input: RequestMediaUploadInput) -> types.PresignedPostCredentials:
    """Request upload credentials for a given key"""

    datalayer = get_current_datalayer()
    key = _scoped_key(info, input.key)

    response = datalayer.s3v4.generate_presigned_post(
        Bucket=settings.MEDIA_BUCKET,
        Key=key,
        Fields=None,
        Conditions=None,
        ExpiresIn=50000,
    )

    path = f"s3://{settings.MEDIA_BUCKET}/{key}"

    store, _ = models.MediaStore.objects.get_or_create(path=path, key=key, bucket=settings.MEDIA_BUCKET)

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
