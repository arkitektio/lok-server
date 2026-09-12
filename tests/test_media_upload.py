"""lok's object-storage surface, against the real store in the compose stack.

This surface had no coverage at all. The fixtures that used to sit in conftest
(`s3`, `create_bucket1`, `create_bucket2`, all moto-backed) were referenced by no
test -- dead template code -- while the code that matters went unexercised:
`request_media_upload` mints a presigned POST for avatar/banner uploads, and
`MediaStore.get_presigned_url` mints a presigned GET to read them back.

Both are only correct if the *store* accepts what was signed, so these talk to a
real RustFS container. A mock hands back plausible strings no server validates.

The mutation is driven through the management schema rather than by calling the
resolver directly, so the auth extension and `_scoped_key` run exactly as in
production. Execution is `await schema.execute(...)`: the schema's extension hooks
are async and `execute_sync` fails with "GraphQL execution failed to complete
synchronously".
"""

import uuid

import pytest
import requests
from asgiref.sync import sync_to_async
from django.conf import settings

from api.management.schema import schema as management_schema
from fakts import models
from tests import factories
from tests.conftest import build_auth_context

REQUEST_MEDIA_UPLOAD = """
    mutation RequestMediaUpload($input: RequestMediaUploadInput!) {
        requestMediaUpload(input: $input) {
            key
            bucket
            store
            policy
            xAmzAlgorithm
            xAmzCredential
            xAmzDate
            xAmzSignature
        }
    }
"""


def _setup():
    """Sync DB setup (must not run inside the async event loop)."""
    membership = factories.make_membership()
    request_client = factories.make_client(membership=membership)
    return membership.user, membership.organization, request_client


def _form(creds: dict, key: str) -> dict:
    """The multipart fields a presigned POST needs, with `key` swappable."""
    return {
        "key": key,
        "x-amz-algorithm": creds["xAmzAlgorithm"],
        "x-amz-credential": creds["xAmzCredential"],
        "x-amz-date": creds["xAmzDate"],
        "x-amz-signature": creds["xAmzSignature"],
        "policy": creds["policy"],
    }


async def _request_upload(context, key: str) -> dict:
    result = await management_schema.execute(
        REQUEST_MEDIA_UPLOAD,
        context_value=context,
        variable_values={"input": {"key": key, "datalayer": "default"}},
    )
    assert not result.errors, result.errors
    return result.data["requestMediaUpload"]


def test_initc_provisioned_the_media_bucket(s3_client):
    """`initc` ran and created the bucket settings_test points at."""
    assert settings.MEDIA_BUCKET in {b["Name"] for b in s3_client.list_buckets()["Buckets"]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_presigned_post_upload_is_accepted_by_the_store(s3_client):
    """`requestMediaUpload` mints a POST form the store actually honours.

    RustFS verifies the policy and signature, so a wrong field comes back as a 4xx.
    This is the assertion moto could not make.
    """
    user, organization, request_client = await sync_to_async(_setup)()
    creds = await _request_upload(build_auth_context(user, organization, request_client), "avatar.png")

    body = b"\x89PNG\r\n\x1a\n not really a png"
    response = await sync_to_async(requests.post)(
        f"{settings.AWS_S3_ENDPOINT_URL}/{creds['bucket']}",
        data=_form(creds, creds["key"]),
        files={"file": ("avatar.png", body)},
        timeout=30,
    )
    assert response.status_code in (200, 204), response.text

    got = await sync_to_async(
        lambda: s3_client.get_object(Bucket=creds["bucket"], Key=creds["key"])["Body"].read()
    )()
    assert got == body


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_upload_key_is_namespaced_under_the_calling_user():
    """The presigned POST may only write under ``users/<id>/``.

    `_scoped_key` exists because an unnamespaced key let any authenticated caller
    overwrite another tenant's media, and made two users uploading "avatar.png"
    collide. Path traversal must not escape the prefix either.
    """
    user, organization, request_client = await sync_to_async(_setup)()
    creds = await _request_upload(
        build_auth_context(user, organization, request_client), "../../etc/passwd"
    )

    assert creds["key"] == f"users/{user.id}/passwd"
    assert ".." not in creds["key"]

    store = await sync_to_async(models.MediaStore.objects.get)(id=creds["store"])
    assert store.key == creds["key"]
    assert store.bucket == settings.MEDIA_BUCKET


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_presigned_post_cannot_write_a_different_key():
    """The grant is bound to one key: reusing the form for another is refused.

    Verified by the store, which is the only thing that can enforce it -- the
    policy is signed, so swapping the key invalidates the signature.
    """
    user, organization, request_client = await sync_to_async(_setup)()
    creds = await _request_upload(build_auth_context(user, organization, request_client), "avatar.png")

    response = await sync_to_async(requests.post)(
        f"{settings.AWS_S3_ENDPOINT_URL}/{creds['bucket']}",
        data=_form(creds, f"users/{user.id}/somebody-elses-file.png"),
        files={"file": ("x.png", b"nope")},
        timeout=30,
    )
    assert response.status_code >= 400, (
        "the store accepted a key the policy did not sign: "
        f"{response.status_code} {response.text}"
    )


@pytest.mark.django_db(transaction=True)
def test_media_store_presigned_url_downloads_the_object(s3_client):
    """`MediaStore.get_presigned_url` produces a URL the store honours."""
    from api.management.datalayer import get_current_datalayer

    key = f"users/1/{uuid.uuid4().hex}.png"
    body = b"the bytes behind the avatar"
    s3_client.put_object(Bucket=settings.MEDIA_BUCKET, Key=key, Body=body)

    store = models.MediaStore.objects.create(
        path=f"s3://{settings.MEDIA_BUCKET}/{key}", key=key, bucket=settings.MEDIA_BUCKET
    )

    # host=None makes get_presigned_url strip the endpoint prefix and return a
    # relative URL, so put it back to address the running store.
    relative = store.get_presigned_url(None, datalayer=get_current_datalayer())
    response = requests.get(settings.AWS_S3_ENDPOINT_URL + relative, timeout=30)
    assert response.status_code == 200, response.text
    assert response.content == body
