"""SP metadata generation for a configured SAML app.

Pure service-provider side (`sp_validation_only`), so it needs no IdP and no crypto.
It exists mainly to pin down something load-bearing and easy to get wrong: lok is served
under a `/lok` path prefix, and python3-saml computes the SP's own URLs independently of
`reverse()`. The ACS/entityId URLs in the metadata are what gets registered with a real
IdP, so a doubled or missing prefix here is a login failure that only shows up during
onboarding.
"""

import xml.etree.ElementTree as ET

import pytest
from django.test import override_settings
from django.urls import reverse

SAML_APP = {
    "client_id": "acme-university",
    "provider_id": "saml:acme-university",
    "name": "Acme University",
    "settings": {
        "verified_email": ["acme.edu"],
        "attribute_mapping": {"uid": ["urn:oasis:names:tc:SAML:attribute:subject-id"]},
        # An inline `idp` block must carry all three keys: allauth indexes
        # idp["x509cert"] directly, so omitting it is a KeyError rather than a
        # graceful default. Use `metadata_url` instead to have them fetched.
        "idp": {
            "entity_id": "https://idp.acme.edu/idp/shibboleth",
            "sso_url": "https://idp.acme.edu/idp/profile/SAML2/Redirect/SSO",
            "x509cert": "",
        },
    },
}

NS = {"md": "urn:oasis:names:tc:SAML:2.0:metadata"}


@pytest.fixture
def saml_settings():
    with override_settings(SOCIALACCOUNT_PROVIDERS={"saml": {"APPS": [SAML_APP]}}):
        yield


def test_saml_urls_carry_the_script_name_prefix_exactly_once():
    """lok sets `force_script_name: lok`, but bakes it into the URLconf rather than
    Django's FORCE_SCRIPT_NAME. Guards against a doubled `/lok/lok/...`."""
    assert reverse("saml_acs", kwargs={"organization_slug": "acme-university"}) == "/lok/accounts/saml/acme-university/acs/"
    assert reverse("saml_metadata", kwargs={"organization_slug": "acme-university"}) == "/lok/accounts/saml/acme-university/metadata/"
    assert reverse("saml_sls", kwargs={"organization_slug": "acme-university"}) == "/lok/accounts/saml/acme-university/sls/"
    assert reverse("saml_login", kwargs={"organization_slug": "acme-university"}) == "/lok/accounts/saml/acme-university/login/"


@pytest.mark.django_db
def test_metadata_advertises_the_right_acs_url(client, saml_settings):
    """The ACS URL handed to the IdP must be the absolute, prefixed one."""
    url = reverse("saml_metadata", kwargs={"organization_slug": "acme-university"})
    response = client.get(url, HTTP_HOST="go.arkitekt.live")

    assert response.status_code == 200
    assert response["Content-Type"] == "text/xml"

    root = ET.fromstring(response.content)
    acs = root.find(".//md:SPSSODescriptor/md:AssertionConsumerService", NS)
    assert acs is not None
    assert acs.get("Location") == "http://go.arkitekt.live/lok/accounts/saml/acme-university/acs/"
    assert acs.get("Binding") == "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"

    # With no explicit sp.entity_id, allauth falls back to the metadata URL.
    assert root.get("entityID") == "http://go.arkitekt.live/lok/accounts/saml/acme-university/metadata/"


@pytest.mark.django_db
def test_metadata_honours_forwarded_https(client, saml_settings):
    """Behind Caddy the request reaches lok over http; the metadata must still
    advertise https, or python3-saml's Destination check fails under `strict`."""
    url = reverse("saml_metadata", kwargs={"organization_slug": "acme-university"})
    response = client.get(url, HTTP_HOST="go.arkitekt.live", HTTP_X_FORWARDED_PROTO="https")

    root = ET.fromstring(response.content)
    acs = root.find(".//md:SPSSODescriptor/md:AssertionConsumerService", NS)
    assert acs.get("Location") == "https://go.arkitekt.live/lok/accounts/saml/acme-university/acs/"


@pytest.mark.django_db
def test_metadata_404s_for_an_unconfigured_organization(client, saml_settings):
    """A slug with no SAML app is a 404, not a 500."""
    url = reverse("saml_metadata", kwargs={"organization_slug": "nosuchorg"})
    assert client.get(url, HTTP_HOST="go.arkitekt.live").status_code == 404
