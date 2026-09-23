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
            contentType
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


def _form(creds: dict, key: str, content_type: str | None = None) -> dict:
    """The multipart fields a presigned POST needs, with `key` and the type swappable."""
    return {
        "key": key,
        "Content-Type": content_type or creds["contentType"],
        "x-amz-algorithm": creds["xAmzAlgorithm"],
        "x-amz-credential": creds["xAmzCredential"],
        "x-amz-date": creds["xAmzDate"],
        "x-amz-signature": creds["xAmzSignature"],
        "policy": creds["policy"],
    }


async def _execute_upload(context, key: str, content_type: str | None = None):
    variables = {"key": key, "datalayer": "default"}
    if content_type is not None:
        variables["contentType"] = content_type
    return await management_schema.execute(
        REQUEST_MEDIA_UPLOAD,
        context_value=context,
        variable_values={"input": variables},
    )


async def _request_upload(context, key: str, content_type: str | None = None) -> dict:
    result = await _execute_upload(context, key, content_type)
    assert not result.errors, result.errors
    return result.data["requestMediaUpload"]


async def _post(creds: dict, body: bytes, content_type: str | None = None):
    return await sync_to_async(requests.post)(
        f"{settings.AWS_S3_ENDPOINT_URL}/{creds['bucket']}",
        data=_form(creds, creds["key"], content_type),
        files={"file": ("upload", body)},
        timeout=30,
    )


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
        build_auth_context(user, organization, request_client), "../../etc/passwd", "image/png"
    )

    assert creds["key"].startswith(f"users/{user.id}/")
    assert ".." not in creds["key"] and "passwd" not in creds["key"]

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


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_reuploading_the_same_filename_gets_a_fresh_key_and_store():
    """The gateway caches media as immutable, so a re-upload must not reuse the key."""
    user, organization, request_client = await sync_to_async(_setup)()
    context = build_auth_context(user, organization, request_client)
    first = await _request_upload(context, "avatar.png")
    second = await _request_upload(context, "avatar.png")

    assert first["key"] != second["key"]
    assert first["store"] != second["store"]
    assert first["key"].endswith(".png") and first["contentType"] == "image/png"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,content_type",
    [("logo.svg", None), ("logo.png", "image/svg+xml"), ("page.html", None), ("blob", None), ("x.png", "text/html")],
)
async def test_non_raster_image_types_are_refused(key, content_type):
    """Script-capable types (SVG, HTML) must never be presigned into the public bucket."""
    user, organization, request_client = await sync_to_async(_setup)()
    result = await _execute_upload(build_auth_context(user, organization, request_client), key, content_type)
    assert result.errors and "Unsupported media type" in result.errors[0].message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_store_rejects_a_content_type_other_than_the_signed_one():
    """The policy pins Content-Type exactly, so the form cannot switch it to text/html."""
    user, organization, request_client = await sync_to_async(_setup)()
    creds = await _request_upload(build_auth_context(user, organization, request_client), "avatar.png")

    response = await _post(creds, b"<script>alert(1)</script>", content_type="text/html")
    assert response.status_code >= 400, f"{response.status_code} {response.text}"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_store_rejects_an_oversized_upload():
    """The policy caps size at UPLOAD_MAX_BYTES, on the management surface too."""
    from karakter.graphql.mutations.upload import UPLOAD_MAX_BYTES

    user, organization, request_client = await sync_to_async(_setup)()
    creds = await _request_upload(build_auth_context(user, organization, request_client), "avatar.png")

    response = await _post(creds, b"\0" * (UPLOAD_MAX_BYTES + 1))
    assert response.status_code >= 400, f"{response.status_code} {response.text}"


@pytest.mark.django_db(transaction=True)
def test_user_avatar_reads_from_the_profile():
    """`User.avatar` delegates to `Profile.avatar` (it used to always return None)."""
    membership = factories.make_membership()
    user, org = membership.user, membership.organization
    store = models.MediaStore.objects.create(
        path=f"s3://{settings.MEDIA_BUCKET}/users/{user.id}/x.png", key=f"users/{user.id}/x.png", bucket=settings.MEDIA_BUCKET
    )
    assert user.avatar is None

    profile = user.profile
    profile.avatar = store
    profile.save()
    user.refresh_from_db()
    assert user.avatar == store


def _owner_setup():
    organization = factories.make_organization()
    membership = factories.make_membership(user=organization.owner, organization=organization)
    context = build_auth_context(organization.owner, organization, factories.make_client(membership=membership))
    return organization, context


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_update_organization_avatar_lands_on_the_organization_profile():
    """`OrganizationProfile.avatar` is the one canonical org logo; `Organization.avatar` is deprecated."""
    from karakter.models import OrganizationProfile

    organization, context = await sync_to_async(_owner_setup)()
    creds = await _request_upload(context, "logo.webp")

    result = await management_schema.execute(
        """mutation ($input: UpdateOrganizationInput!) { updateOrganization(input: $input) { id } }""",
        variable_values={"input": {"id": str(organization.id), "avatar": creds["store"]}},
        context_value=context,
    )
    assert not result.errors, result.errors

    profile = await sync_to_async(OrganizationProfile.objects.get)(organization_id=organization.id)
    assert str(profile.avatar_id) == str(creds["store"])


def _attach_avatars():
    """An owner with a profile avatar and an organization whose profile carries a logo."""
    organization, context = _owner_setup()
    owner = organization.owner
    store = models.MediaStore.objects.create(
        path=f"s3://{settings.MEDIA_BUCKET}/users/{owner.id}/a.png", key=f"users/{owner.id}/a.png", bucket=settings.MEDIA_BUCKET
    )
    return organization, context, store


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_user_avatar_resolves_through_the_management_schema():
    """kontrol's ListUser/DetailUser select `avatar`; the sync resolver must run under async execution."""
    organization, context, store = await sync_to_async(_attach_avatars)()
    query = "query { me { id avatar } }"

    result = await management_schema.execute(query, context_value=context)
    assert not result.errors, result.errors
    assert result.data["me"]["avatar"] is None

    def _set():
        profile = organization.owner.profile
        profile.avatar = store
        profile.save()

    await sync_to_async(_set)()
    result = await management_schema.execute(query, context_value=context)
    assert not result.errors, result.errors
    assert result.data["me"]["avatar"].startswith(f"/{settings.MEDIA_BUCKET}/{store.key}?")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_organization_avatar_resolves_from_the_profile_on_the_main_schema():
    from karakter.models import OrganizationProfile
    from lok_server.schema import schema as main_schema

    organization, context, store = await sync_to_async(_attach_avatars)()
    query = "query ($id: ID!) { organization(id: $id) { id avatar { key } } }"
    variables = {"id": str(organization.id)}

    result = await main_schema.execute(query, variable_values=variables, context_value=context)
    assert not result.errors, result.errors
    assert result.data["organization"]["avatar"] is None

    await sync_to_async(OrganizationProfile.objects.filter(organization=organization).update)(avatar=store)
    result = await main_schema.execute(query, variable_values=variables, context_value=context)
    assert not result.errors, result.errors
    assert result.data["organization"]["avatar"] == {"key": store.key}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_profile_bio_can_be_set_and_cleared_through_the_management_schema():
    """kontrol's Profile page sends `bio` on create and update; the inputs used to reject it."""
    organization, context = await sync_to_async(_owner_setup)()
    owner = organization.owner
    profile_id = await sync_to_async(lambda: owner.profile.id)()

    created = await management_schema.execute(
        "mutation ($input: CreateProfileInput!) { createProfile(input: $input) { id name bio } }",
        variable_values={"input": {"user": str(owner.id), "name": "Ada", "bio": "hello"}},
        context_value=context,
    )
    assert not created.errors, created.errors
    assert created.data["createProfile"]["bio"] == "hello"

    update = "mutation ($input: UpdateProfileInput!) { updateProfile(input: $input) { bio } }"
    for bio in ("new bio", ""):
        result = await management_schema.execute(
            update, variable_values={"input": {"id": str(profile_id), "bio": bio}}, context_value=context
        )
        assert not result.errors, result.errors
        assert result.data["updateProfile"]["bio"] == bio


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_membership_is_owner_marks_only_the_owner():
    """kontrol hides "Remove from organization" on the owner's membership, which lok refuses to delete."""
    def _setup_two():
        organization, context = _owner_setup()
        member = factories.make_membership(organization=organization)
        owner_membership = organization.memberships.get(user=organization.owner)
        return context, owner_membership, member

    context, owner_membership, member = await sync_to_async(_setup_two)()
    query = "query ($id: ID!) { membership(id: $id) { id isOwner } }"
    for membership, expected in ((owner_membership, True), (member, False)):
        result = await management_schema.execute(query, variable_values={"id": str(membership.id)}, context_value=context)
        assert not result.errors, result.errors
        assert result.data["membership"]["isOwner"] is expected
