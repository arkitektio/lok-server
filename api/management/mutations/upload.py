from kante.types import Info
import strawberry

from fakts import types
from api.management.authz import get_user
from api.management.datalayer import get_current_datalayer
from karakter.graphql.mutations.upload import presign_media_upload


@strawberry.input()
class RequestMediaUploadInput:
    key: str
    datalayer: str
    content_type: str | None = strawberry.field(
        default=None,
        description="MIME type of the file. The upload must send the same Content-Type. Guessed from `key` when omitted.",
    )


def request_media_upload(info: Info, input: RequestMediaUploadInput) -> types.PresignedPostCredentials:
    """Request upload credentials for a given key.

    Shares `presign_media_upload` with the main schema so the key namespacing,
    size cap, content-type allowlist and expiry cannot drift between the two.
    """
    return presign_media_upload(get_user(info), get_current_datalayer(), input.datalayer, input.key, input.content_type)
