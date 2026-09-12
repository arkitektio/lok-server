"""Tests for authapp JWT/OIDC token generation, including the client_role claim."""

import pytest
from django.conf import settings

from authapp.token_generators import MyJWTBearerTokenGenerator
from fakts.enums import ClientRoleChoices
from tests import factories


def _generator():
    return MyJWTBearerTokenGenerator(issuer=settings.OIDC_ISSUER)


@pytest.mark.django_db
def test_get_extra_claims_includes_client_role_and_org():
    membership = factories.make_membership()
    oauth2 = factories.make_oauth2_client(membership=membership)
    factories.make_client(membership=membership, oauth2_client=oauth2, role=ClientRoleChoices.AGENT.value)

    claims = _generator().get_extra_claims(oauth2, "client_credentials", membership, None)

    assert claims["client_role"] == "agent"
    assert claims["org"] == str(membership.organization_id)
    assert "active_org" not in claims
    assert claims["sub"] == str(membership.user.id)
    assert claims["preferred_username"] == membership.user.username


@pytest.mark.django_db
def test_get_extra_claims_defaults_role_to_interface():
    membership = factories.make_membership()
    oauth2 = factories.make_oauth2_client(membership=membership)
    factories.make_client(membership=membership, oauth2_client=oauth2)

    claims = _generator().get_extra_claims(oauth2, "client_credentials", membership, None)
    assert claims["client_role"] == "interface"


@pytest.mark.django_db
def test_get_audiences_for_non_fakts_client_is_the_client():
    """Plain OIDC relying parties get themselves as the audience (RFC 9068)."""
    membership = factories.make_membership()
    oauth2 = factories.make_oauth2_client(membership=membership)

    assert _generator().get_audiences(oauth2, membership, None) == [oauth2.client_id]


@pytest.mark.django_db
def test_get_audiences_for_fakts_client_lists_mapped_instance_ids():
    """A fakts client's audiences are the *instance ids* it was composed with, plus lok."""
    membership = factories.make_membership()
    oauth2 = factories.make_oauth2_client(membership=membership)
    fakts_client = factories.make_client(membership=membership, oauth2_client=oauth2)

    from fakts import models as fmodels

    instance = factories.make_service_instance()
    fmodels.ServiceInstanceMapping.objects.create(client=fakts_client, instance=instance, key="db")

    audiences = _generator().get_audiences(oauth2, membership, None)
    assert audiences == ["lok", str(instance.pk)]


def test_get_jwks_exposes_signing_key():
    jwks = _generator().get_jwks()
    # get_jwks returns a JWK *set* so joserfc can stamp the key's kid into the
    # JWT header; the signing key is the sole entry under "keys".
    key = jwks["keys"][0]
    assert key["kty"] == "RSA"
    assert key["use"] == "sig"
    assert key["kid"] == settings.KEY_ID
    # public modulus/exponent are present so consumers can verify signatures
    assert key["n"] and key["e"]


@pytest.mark.django_db
def test_openid_user_info_contains_subject():
    from authapp.grants import OpenIDCode

    membership = factories.make_membership()
    info = OpenIDCode().generate_user_info(membership, "openid profile email")

    assert info["sub"] == str(membership.user.id)
    assert info["org"] == str(membership.organization_id)
    assert "active_org" not in info


@pytest.mark.django_db
def test_audiences_distinguish_two_tenants_running_the_same_service():
    """The bug the instance-id switch closes.

    `Service.identifier` is unique only *per organization* ("Only one service
    identifier per organization"), so two tenants both running e.g.
    `@mikro/mikro` used to mint tokens carrying the *same* audience value — and a
    resource server checking `aud` against its own identifier would have accepted
    a token issued for the other tenant's instance.
    """
    from fakts import models as fmodels

    def _client_with_instance(identifier):
        membership = factories.make_membership()
        oauth2 = factories.make_oauth2_client(membership=membership)
        fakts_client = factories.make_client(membership=membership, oauth2_client=oauth2)
        instance = factories.make_service_instance()
        # Force both tenants' services to share an identifier.
        service = instance.release.service
        service.identifier = identifier
        service.save(update_fields=["identifier"])
        fmodels.ServiceInstanceMapping.objects.create(
            client=fakts_client, instance=instance, key="db"
        )
        return oauth2, membership, service.identifier

    a_client, a_membership, ident = _client_with_instance("com.example.shared")
    b_client, b_membership, _ = _client_with_instance("com.example.shared")

    a_aud = _generator().get_audiences(a_client, a_membership, None)
    b_aud = _generator().get_audiences(b_client, b_membership, None)

    assert a_aud != b_aud, "two tenants' tokens share an audience"
    assert a_aud[0] == b_aud[0] == "lok"
