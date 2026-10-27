"""A hub's instance claims say which of its services each is: the name the hub gave it, and its service.

The hub names its services itself (``mikro``), so the claims are keyed by those names, and a
claim's ``id`` is the same name: the ``aud`` a token composed for it carries (with the hub's own
uid as ``hub``), and the key a guest's ``collabs`` lists its collaborations under.
"""

import pytest
from django.conf import settings

from authapp.token_generators import MyJWTBearerTokenGenerator
from fakts import models
from karakter import models as karakter_models
from tests import factories
from tests.test_instance_keys import _enroll, _instance


@pytest.fixture
def admin():
    org = factories.make_organization()
    return factories.make_membership(user=org.owner, organization=org)


@pytest.mark.django_db
def test_each_instance_claim_is_keyed_by_the_name_the_hub_gave_it(client, admin):
    hub, token = _enroll(client, admin, [_instance("mikro", None), _instance("rekuest", None)])

    claims = token["instances"]
    assert len(claims) == 2
    by_service = {claim["service"]: claim for claim in claims.values()}
    assert set(by_service) == {"live.arkitekt.mikro", "live.arkitekt.rekuest"}
    assert set(claims) == {"mikro", "rekuest"}  # as the hub named them
    for key, claim in claims.items():
        instance = hub.instances.get(instance_id=key)
        assert claim["identifier"] == claim["id"] == key
        assert claim["service"] == instance.release.service.identifier


@pytest.mark.django_db
def test_the_claimed_id_is_the_audience_a_token_for_the_instance_carries(client, admin):
    hub, token = _enroll(client, admin, [_instance("mikro", None)])
    (claim,) = token["instances"].values()

    membership = factories.make_membership()
    oauth2 = factories.make_oauth2_client(membership=membership)
    app = factories.make_client(membership=membership, oauth2_client=oauth2)
    models.ServiceInstanceMapping.objects.create(client=app, instance=hub.instances.get(), key="mikro")

    audiences = MyJWTBearerTokenGenerator(issuer=settings.OIDC_ISSUER).get_audiences(oauth2, membership, None)
    assert claim["id"] in audiences


def _versioned(name: str, version: str) -> dict:
    instance = _instance(name, None)
    instance["manifest"]["version"] = version
    return instance


@pytest.mark.django_db
def test_authorized_again_by_another_admin_the_instance_and_its_id_stay(client, admin):
    hub, token = _enroll(client, admin, [_instance("mikro", None)])
    (claim,) = token["instances"].values()

    other = factories.make_membership(organization=admin.organization)
    other.roles.add(karakter_models.Role.objects.get_or_create(organization=admin.organization, identifier="admin")[0])
    again_hub, again = _enroll(client, other, [_instance("mikro", None)])
    assert again_hub.pk == hub.pk
    (same,) = again["instances"].values()
    assert same["id"] == claim["id"]
    instance = hub.instances.get()
    assert instance.steward == other.user  # who vouched for it last


@pytest.mark.django_db
def test_a_new_release_is_the_same_instance(client, admin):
    hub, token = _enroll(client, admin, [_versioned("mikro", "1.0.0")])
    (claim,) = token["instances"].values()

    _, again = _enroll(client, admin, [_versioned("mikro", "2.0.0")])
    (same,) = again["instances"].values()
    assert same["id"] == claim["id"]
    instance = hub.instances.get()
    assert instance.release.version == "2.0.0" and instance.service.identifier == "live.arkitekt.mikro"


FAKTS_BEFORE = [("fakts", "0005_device_code_polling_client_id"), ("karakter", "0005_collaboration_grants")]


