"""End-to-end: a SAML assertion at the ACS endpoint registers and logs in a user.

A POST to ``/lok/accounts/saml/acme-university/acs/`` goes through allauth's views and
produces a `User` with a linked `SocialAccount`. SAML is an authentication method and
nothing more — organizations are orthogonal to it, so the user comes out with only the
personal organization every signup gets, and joins real organizations by invite.

The assertion XML is adapted from django-allauth's own SAML test fixtures. Those ship
in the sdist but not the wheel, so it is vendored here rather than imported. Signature
validation is patched out and the app runs with ``strict: False``, so there is no
crypto, no certificate and no clock dependence to keep working.
"""

import base64
from http import HTTPStatus
from unittest.mock import patch

import pytest
from allauth.socialaccount.models import SocialAccount
from django.test import Client, override_settings
from django.urls import reverse

from karakter.models import Membership, User
from karakter.slugs import validate_slug

HOST = "example.com"
# The URL segment is the app's client_id — an identity-provider name, not an
# organization slug. Nothing about SAML resolves an Organization.
ACS_URL = f"http://{HOST}/lok/accounts/saml/acme-university/acs/"
AUDIENCE = f"http://{HOST}/lok/accounts/saml/acme-university/metadata/"

# `reject_idp_initiated_sso: False` lets the test post an unsolicited assertion, which
# skips the SP-initiated state dance while exercising the same ACS -> finish -> login path.
SAML_APP = {
    "client_id": "acme-university",
    "provider_id": "saml:acme-university",
    "name": "Acme University",
    "settings": {
        "verified_email": ["email.org"],
        "attribute_mapping": {
            "uid": "http://schemas.auth0.com/clientID",
            "email_verified": "http://schemas.auth0.com/email_verified",
            "email": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
        },
        "idp": {
            "name": "Test IdP",
            "entity_id": "urn:dev-123.us.auth0.com",
            "sso_url": "https://dev-123.us.auth0.com/samlp/456",
            "slo_url": "https://dev-123.us.auth0.com/samlp/456",
            "x509cert": "",
        },
        "advanced": {"strict": False, "reject_idp_initiated_sso": False},
    },
}

SAML_RESPONSE_XML = f"""<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" ID="id123" Version="2.0" IssueInstant="2023-07-08T08:24:14.141Z" Destination="{ACS_URL}">
  <saml:Issuer xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">urn:dev-123.us.auth0.com</saml:Issuer>
  <samlp:Status>
    <samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/>
  </samlp:Status>
  <saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" Version="2.0" ID="id321" IssueInstant="2023-07-08T08:24:14.094Z">
    <saml:Issuer>urn:dev-123.us.auth0.com</saml:Issuer>
    <!-- python3-saml requires a Signature element to be present even when
         signature validation itself is mocked out; its contents are never checked. -->
    <Signature xmlns="http://www.w3.org/2000/09/xmldsig#">
      <SignedInfo>
        <CanonicalizationMethod Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"/>
        <SignatureMethod Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"/>
        <Reference URI="#id321">
          <Transforms>
            <Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"/>
            <Transform Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"/>
          </Transforms>
          <DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>
          <DigestValue>A321</DigestValue>
        </Reference>
      </SignedInfo>
      <SignatureValue>MTIz</SignatureValue>
      <KeyInfo>
        <X509Data>
          <X509Certificate>MIIDHTCC...</X509Certificate>
        </X509Data>
      </KeyInfo>
    </Signature>
    <saml:Subject>
      <saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified">google-oauth2|108204123456789</saml:NameID>
      <saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">
        <saml:SubjectConfirmationData NotOnOrAfter="2123-07-08T09:24:14.094Z" Recipient="{ACS_URL}"/>
      </saml:SubjectConfirmation>
    </saml:Subject>
    <saml:Conditions NotBefore="2023-07-08T08:24:14.094Z" NotOnOrAfter="2123-07-08T09:24:14.094Z">
      <saml:AudienceRestriction>
        <saml:Audience>{AUDIENCE}</saml:Audience>
      </saml:AudienceRestriction>
    </saml:Conditions>
    <saml:AuthnStatement AuthnInstant="2023-07-08T08:24:14.094Z" SessionIndex="_qPrYdL0O8w3vdb8eCEY5ZtHe76LA8-JU">
      <saml:AuthnContext>
        <saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:unspecified</saml:AuthnContextClassRef>
      </saml:AuthnContext>
    </saml:AuthnStatement>
    <saml:AttributeStatement xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <saml:Attribute Name="http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress" NameFormat="urn:oasis:names:tc:SAML:2.0:attrname-format:uri">
        <saml:AttributeValue xsi:type="xs:string">john.doe@email.org</saml:AttributeValue>
      </saml:Attribute>
      <saml:Attribute Name="http://schemas.auth0.com/clientID" NameFormat="urn:oasis:names:tc:SAML:2.0:attrname-format:uri">
        <saml:AttributeValue xsi:type="xs:string">dummysamluid</saml:AttributeValue>
      </saml:Attribute>
      <saml:Attribute Name="http://schemas.auth0.com/email_verified" NameFormat="urn:oasis:names:tc:SAML:2.0:attrname-format:uri">
        <saml:AttributeValue xsi:type="xs:boolean">true</saml:AttributeValue>
      </saml:Attribute>
    </saml:AttributeStatement>
  </saml:Assertion>
</samlp:Response>
"""


@pytest.fixture
def saml_response():
    return base64.b64encode(SAML_RESPONSE_XML.encode("utf8")).decode("utf8")


@pytest.fixture
def mocked_signature_validation():
    with patch("onelogin.saml2.utils.OneLogin_Saml2_Utils.validate_sign") as mock:
        mock.return_value = True
        yield


def post_assertion(client, saml_response):
    """Drive ACS -> finish, the two-step redirect allauth uses to get the IdP's
    cross-site POST onto a same-site GET before logging the user in."""
    slug = {"organization_slug": "acme-university"}  # allauth's kwarg name; it is the client_id
    response = client.post(reverse("saml_acs", kwargs=slug), data={"SAMLResponse": saml_response})
    assert response.status_code == HTTPStatus.FOUND

    finish = reverse("saml_finish_acs", kwargs=slug)
    assert response["location"] == finish
    return client.get(finish)


@pytest.mark.django_db
@override_settings(SOCIALACCOUNT_PROVIDERS={"saml": {"APPS": [SAML_APP]}})
def test_saml_assertion_registers_and_logs_in_a_user(saml_response, mocked_signature_validation):
    post_assertion(Client(HTTP_HOST=HOST), saml_response)

    user = User.objects.get(socialaccount__uid="dummysamluid")

    # SocialAccount.provider stores the app's provider_id (sub_id), not "saml" —
    # which is exactly why the GraphQL `provider` field had to become a plain str.
    account = SocialAccount.objects.get(user=user)
    assert account.provider == "saml:acme-university"


@pytest.mark.django_db
@override_settings(SOCIALACCOUNT_PROVIDERS={"saml": {"APPS": [SAML_APP]}})
def test_saml_login_grants_no_organization_membership(saml_response, mocked_signature_validation):
    """Standing guard against organization auto-provisioning creeping back in.

    An earlier design made a SAML app's client_id an Organization slug and granted
    membership on login, so an organization gained members without anyone in it
    approving. SAML now only authenticates: the user gets the personal organization
    every signup gets, and nothing else. Real organizations are joined by invite.
    """
    post_assertion(Client(HTTP_HOST=HOST), saml_response)

    user = User.objects.get(socialaccount__uid="dummysamluid")
    memberships = Membership.objects.filter(user=user).select_related("organization")

    # The invariant is "their own personal organization, and nothing else".
    # Assert that directly rather than pinning a derived slug string: the slug is
    # now normalised through `slugs.slugify_name` (a username like "john.doe"
    # previously produced "john.doe-org", which `SLUG_RE` does not even accept),
    # and it is suffixed on collision so signup can never join an existing org.
    assert memberships.count() == 1
    only = memberships.get().organization
    assert only.owner_id == user.pk
    validate_slug(only.slug)


@pytest.mark.django_db
@override_settings(SOCIALACCOUNT_PROVIDERS={"saml": {"APPS": [SAML_APP]}})
def test_second_saml_login_is_idempotent(saml_response, mocked_signature_validation):
    """Logging in again must not duplicate the user or the linked account."""
    post_assertion(Client(HTTP_HOST=HOST), saml_response)
    post_assertion(Client(HTTP_HOST=HOST), saml_response)

    user = User.objects.get(socialaccount__uid="dummysamluid")
    assert SocialAccount.objects.filter(user=user).count() == 1
    assert Membership.objects.filter(user=user).count() == 1


