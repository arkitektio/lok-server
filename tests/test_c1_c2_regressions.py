"""Regressions for the two critical findings of the 2026-08-21 security review.

C1 — signup must never join an organization that already exists.
C2 — the main GraphQL API must reject a token not issued by, or not addressed
     to, this server.
"""

import pytest
from asgiref.sync import sync_to_async
from django.conf import settings

from authapp.extension import assert_addressed_to_lok
from authentikate.base_models import StaticToken
from authentikate.errors import InvalidJwtTokenError
from karakter.models import Membership, Organization, User


# --------------------------------------------------------------------------- #
# C1
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_signup_does_not_join_existing_organization():
    """The finding: `get_or_create(slug=f"{username}-org")` matched on slug alone
    and then granted `admin` outside the `if created` branch, so registering the
    username `acme` made you an admin of the pre-existing org `acme-org`.
    """
    victim_owner = User.objects.create(username="victim-owner")
    victim_org = Organization.objects.get_or_create(
        slug="acme-org", defaults={"name": "Acme", "owner": victim_owner}
    )[0]

    attacker = User.objects.create(username="acme")

    assert not Membership.objects.filter(
        user=attacker, organization=victim_org
    ).exists(), "signup joined the attacker to a pre-existing organization"

    # They still get their own org, just not the victim's.
    own = Organization.objects.filter(owner=attacker)
    assert own.exists()
    assert own.first().pk != victim_org.pk
    assert own.first().slug != "acme-org"


@pytest.mark.django_db
def test_signup_still_provisions_an_admin_org():
    user = User.objects.create(username="freshuser")
    org = Organization.objects.get(owner=user)
    membership = Membership.objects.get(user=user, organization=org)
    assert "admin" in {r.identifier for r in membership.roles.all()}


# --------------------------------------------------------------------------- #
# C2
# --------------------------------------------------------------------------- #


def _token(**overrides):
    claims = dict(
        sub="1",
        iss=settings.OIDC_ISSUER,
        aud=["lok"],
        client_id="some-client",
        org="1",  # org pk; see authapp.extension.read_org_claim
    )
    claims.update(overrides)
    return StaticToken(**claims)


def test_token_addressed_to_lok_is_accepted():
    assert_addressed_to_lok(_token())


def test_relying_party_token_is_rejected():
    """The finding: `get_audiences` mints `[client_id]` for a plain OIDC relying
    party — explicitly not `lok` — but the main schema never checked `aud`, so
    an RP's access token authenticated as its user.
    """
    with pytest.raises(InvalidJwtTokenError):
        assert_addressed_to_lok(_token(aud=["some-relying-party"]))


def test_token_without_audience_is_rejected():
    """An audience-less token names no service, so it must never authenticate.

    In authentikate v4 this is enforced a layer earlier than lok's own gate:
    `aud` is a required `list[str]` whose validator coerces anything falsy to
    None, so a token carrying no audience cannot even be *modelled* — and
    `authentikate.decode._validate_claims` marks `aud` essential, rejecting it
    before an `AuthAppExtension` is ever handed one. Asserting that here keeps
    the C2 property covered at the layer that now owns it; the lok-side half
    (an audience that exists but isn't ours) is `test_relying_party_token_is_rejected`.
    """
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        _token(aud=[])


def test_token_from_a_foreign_issuer_is_rejected():
    with pytest.raises(InvalidJwtTokenError):
        assert_addressed_to_lok(_token(iss="https://evil.example"))


# --------------------------------------------------------------------------- #
# Organization identity: pk, not slug
# --------------------------------------------------------------------------- #


def _jwt_token(**overrides):
    """A `JWTToken` — the model production actually authenticates with.

    `StaticToken` defaults `org` to "static_org", so a static fixture cannot show
    that the claim is really being read off the payload. `JWTToken` requires it.
    """
    import datetime

    from authentikate.base_models import JWTToken

    now = datetime.datetime.now(datetime.timezone.utc)
    claims = dict(
        sub="1",
        iss=settings.OIDC_ISSUER,
        aud=["lok"],
        client_id="some-client",
        org="1",
        raw="header.payload.signature",
        iat=now,
        exp=now + datetime.timedelta(hours=1),
        scope="openid",
        roles=["admin"],
        preferred_username="someone",
    )
    claims.update(overrides)
    return JWTToken(**claims)


def test_read_org_claim_rejects_a_token_naming_no_organization():
    from authapp.extension import read_org_claim

    with pytest.raises(InvalidJwtTokenError):
        # `StaticToken` narrows `org` to a plain `str`, so the empty string is
        # how "no organization" is expressed here.
        read_org_claim(_token(org=""))


def test_read_org_claim_reads_the_declared_org_claim():
    """`org` is a field authentikate declares (v4), so it is parsed off the
    signature-verified payload like any other claim. This used to be read by
    hand-decoding `token.raw`, because the library dropped undeclared claims —
    so assert it on a real `JWTToken`, and that an undeclared claim is still
    dropped (which is why the hand-decode existed in the first place).
    """
    from authapp.extension import read_org_claim

    token = _jwt_token(org="4242", some_undeclared_claim="dropped")
    assert read_org_claim(token) == "4242"
    assert not hasattr(token, "some_undeclared_claim")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_extension_resolves_the_organization_by_pk_from_a_real_token():
    """The production resolution path: `org` claim -> Organization pk lookup,
    end to end through the extension rather than `read_org_claim` alone.
    """
    from asgiref.sync import sync_to_async

    from authapp.extension import AuthAppExtension

    org = await sync_to_async(Organization.objects.create)(
        slug="pk-resolution", name="PK Resolution", owner=await sync_to_async(User.objects.create)(username="pkowner")
    )

    resolved = await AuthAppExtension().aexpand_organization_from_token(_jwt_token(org=str(org.pk)))
    assert resolved.pk == org.pk


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_extension_fails_closed_on_an_unknown_or_malformed_org():
    from authapp.extension import AuthAppExtension

    for org in ("99999999", "not-a-pk"):
        with pytest.raises(InvalidJwtTokenError):
            await AuthAppExtension().aexpand_organization_from_token(_jwt_token(org=org))


@pytest.mark.django_db
def test_org_claim_survives_a_real_mint_decode_roundtrip():
    """The seam `read_org_claim` depends on, end to end.

    `read_org_claim` used to hand-decode `token.raw`, precisely because
    authentikate dropped claims it did not declare. It now reads `token.org`, so
    the org claim only reaches lok if the library really parses it off the
    payload — something no other test covers, since every authenticated fixture
    builds a `StaticToken` directly and never goes through `decode_token`.

    Mint a token the way lok does, verify it the way lok does, and assert the
    organization pk comes out the far end.
    """
    import time

    from authentikate.decode import decode_token
    from authentikate.utils import get_settings
    from authlib.jose import jwt as jose_jwt

    from authapp.extension import read_org_claim
    from authapp.token_generators import MyJWTBearerTokenGenerator
    from tests import factories

    membership = factories.make_membership()
    oauth2 = factories.make_oauth2_client(membership=membership)
    factories.make_client(membership=membership, oauth2_client=oauth2)

    claims = MyJWTBearerTokenGenerator(issuer=settings.OIDC_ISSUER).get_extra_claims(
        oauth2, "client_credentials", membership, None
    )

    # Sign as whichever issuer authentikate is configured to trust, so this test
    # exercises the parsing seam rather than re-asserting the issuer config.
    authentikate_settings = get_settings()
    now = int(time.time())
    raw = jose_jwt.encode(
        {"alg": "RS256", "kid": settings.KEY_ID},
        {
            **claims,
            "iss": authentikate_settings.issuers[0].iss,
            "aud": ["lok"],
            "iat": now,
            "exp": now + 3600,
            "client_id": oauth2.client_id,
        },
        settings.PRIVATE_KEY,
    ).decode()

    token = decode_token(raw, authentikate_settings)

    assert token.org == str(membership.organization_id)
    assert read_org_claim(token) == str(membership.organization_id)
