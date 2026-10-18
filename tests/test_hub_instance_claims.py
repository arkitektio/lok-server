"""A hub's instance claims say which instance each is: its id and its service.

The hub server keys its config by the claim's opaque ``identifier`` (the instance token),
which names no service. A service verifies tokens for its own instance: the ``aud`` a token
composed for it carries, and the key a guest's ``collabs`` lists its collaborations under,
are both the instance's id. The claim carries that id, and the service it is an instance of,
by which the hub tells which of its services the claim is for.
"""

import pytest
from django.conf import settings

from authapp.token_generators import MyJWTBearerTokenGenerator
from fakts import models
from tests import factories
from tests.test_instance_keys import _enroll, _instance


@pytest.fixture
def admin():
    org = factories.make_organization()
    return factories.make_membership(user=org.owner, organization=org)


@pytest.mark.django_db
def test_each_instance_claim_carries_its_id_and_service(client, admin):
    hub, token = _enroll(client, admin, [_instance("mikro", None), _instance("rekuest", None)])

    claims = token["instances"]
    assert len(claims) == 2
    by_service = {claim["service"]: claim for claim in claims.values()}
    assert set(by_service) == {"live.arkitekt.mikro", "live.arkitekt.rekuest"}
    for key, claim in claims.items():
        instance = hub.instances.get(token=key)
        assert claim["identifier"] == instance.token
        assert claim["id"] == str(instance.pk)
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
