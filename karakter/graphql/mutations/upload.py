import mimetypes
import uuid
from pathlib import PurePosixPath

from kante.types import Info
import strawberry

from fakts import types, models
from karakter.authz import get_user
from karakter.datalayer import get_current_datalayer
from django.conf import settings
from graphql import GraphQLError

# A presigned POST grants write access to exactly one key, so the key must not be
# attacker-chosen. `MEDIA_BUCKET` is served publicly by the gateway, so an
# unnamespaced key also meant arbitrary content hosted on the deployment's domain.
# The management schema's `requestMediaUpload` delegates to `presign_media_upload`
# below, so these bounds hold for both surfaces.
UPLOAD_MAX_BYTES = 10 * 1024 * 1024
UPLOAD_EXPIRES_IN = 3600
# Media uploads are avatars, banners and logos. `MEDIA_BUCKET` is served from the
# deployment's own origin, so anything that can carry script (HTML, and SVG even
# though it is image/*) must never land there. POST policies cannot express
# "image/* except svg", so the exact type is pinned with an `eq` condition.
UPLOAD_ALLOWED_CONTENT_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif"})


@strawberry.input()
class RequestMediaUploadInput:
    key: str
    datalayer: str
    content_type: str | None = strawberry.field(
        default=None,
        description="MIME type of the file. The upload must send the same Content-Type. Guessed from `key` when omitted.",
    )


def resolve_content_type(key: str, content_type: str | None) -> str:
    """The MIME type the presign pins, or a GraphQLError if it is not an allowed image type."""
    resolved = (content_type or mimetypes.guess_type(key)[0] or "").lower()
    if resolved not in UPLOAD_ALLOWED_CONTENT_TYPES:
        raise GraphQLError(
            f"Unsupported media type '{resolved or 'unknown'}'. Allowed: {', '.join(sorted(UPLOAD_ALLOWED_CONTENT_TYPES))}."
        )
    return resolved


def scoped_key(user, content_type: str) -> str:
    """A fresh object key under the calling user's namespace.

    The client's filename is not used at all, so it cannot escape the prefix.
    Every upload gets a new random name: the gateway caches media objects as
    immutable, so reusing a key such as ``avatar.png`` would keep serving the old
    image, and would re-point an existing `MediaStore` row at new bytes. The
    ``users/<id>/`` prefix is what `resolve_own_media_store` checks ownership against.
    """
    return f"users/{user.id}/{uuid.uuid4().hex}{mimetypes.guess_extension(content_type) or ''}"


def presign_media_upload(user, datalayer, datalayer_name: str, key: str, content_type: str | None = None) -> types.PresignedPostCredentials:
    """Mint a bounded presigned POST for one new media object owned by `user`."""
    content_type = resolve_content_type(key, content_type)
    key = scoped_key(user, content_type)

    response = datalayer.s3v4.generate_presigned_post(
        Bucket=settings.MEDIA_BUCKET,
        Key=key,
        Fields=None,
        # Bound what the presign authorizes: without these conditions the holder
        # can upload an object of any size and any type.
        Conditions=[
            ["content-length-range", 0, UPLOAD_MAX_BYTES],
            ["eq", "$Content-Type", content_type],
        ],
        ExpiresIn=UPLOAD_EXPIRES_IN,
    )

    path = f"s3://{settings.MEDIA_BUCKET}/{key}"

    store = models.MediaStore.objects.create(path=path, key=key, bucket=settings.MEDIA_BUCKET)

    aws = {
        "key": response["fields"]["key"],
        "x_amz_algorithm": response["fields"]["x-amz-algorithm"],
        "x_amz_credential": response["fields"]["x-amz-credential"],
        "x_amz_date": response["fields"]["x-amz-date"],
        "x_amz_signature": response["fields"]["x-amz-signature"],
        "policy": response["fields"]["policy"],
        "bucket": settings.MEDIA_BUCKET,
        "datalayer": datalayer_name,
        "store": store.id,
        "content_type": content_type,
    }

    return types.PresignedPostCredentials(**aws)


def request_media_upload(
    info: Info, input: RequestMediaUploadInput
) -> types.PresignedPostCredentials:
    """Request upload credentials for a given key"""
    return presign_media_upload(get_user(info), get_current_datalayer(), input.datalayer, input.key, input.content_type)
