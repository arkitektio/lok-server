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
from karakter import models as karakter_models
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


@pytest.fixture
def back_to_latest():
    yield
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.mark.django_db(transaction=True)
def test_the_migration_merges_a_services_duplicate_instances_into_the_newest(back_to_latest):
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(FAKTS_BEFORE)
    apps = executor.loader.project_state(FAKTS_BEFORE).apps
    User = apps.get_model("karakter", "User")
    Organization = apps.get_model("karakter", "Organization")
    Membership = apps.get_model("karakter", "Membership")
    Grant = apps.get_model("karakter", "CollaborationGrant")
    Hub = apps.get_model("fakts", "Hub")
    Service = apps.get_model("fakts", "Service")
    Release = apps.get_model("fakts", "ServiceRelease")
    Instance = apps.get_model("fakts", "ServiceInstance")
    Client = apps.get_model("fakts", "Client")
    Mapping = apps.get_model("fakts", "ServiceInstanceMapping")

    first, second = User.objects.create(username="first-admin"), User.objects.create(username="second-admin")
    org = Organization.objects.create(owner=first, slug="dup", name="Dup")
    guest = Membership.objects.create(user=User.objects.create(username="dup-guest"), organization=org)
    hub = Hub.objects.create(name="h", organization=org, identifier="h", creator=first, token="hub-token")
    service = Service.objects.create(identifier="live.arkitekt.mikro", organization=org, name="mikro")
    old_release, new_release = Release.objects.create(service=service, version="1.0.0"), Release.objects.create(service=service, version="2.0.0")
    old = Instance.objects.create(hub=hub, release=old_release, steward=first, organization=org, template="{}", token="a", instance_id="default")
    new = Instance.objects.create(hub=hub, release=new_release, steward=second, organization=org, template="{}", token="b", instance_id="default")
    app = Client.objects.create(client_id="dup-client")
    Mapping.objects.create(client=app, instance=old, key="mikro")
    Grant.objects.create(membership=guest, instance=old, collaboration_id="5", roles=["editor"])
    Grant.objects.create(membership=guest, instance=new, collaboration_id="5", roles=["viewer"])
    Grant.objects.create(membership=guest, instance=old, collaboration_id="9", roles=[])

    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())

    (kept,) = models.ServiceInstance.objects.filter(hub_id=hub.pk)
    assert kept.pk == new.pk and kept.service_id == service.pk
    grants = {g.collaboration_id: g.roles for g in karakter_models.CollaborationGrant.objects.filter(membership_id=guest.pk, instance=kept)}
    assert grants == {"5": ["editor", "viewer"], "9": []}
    assert models.ServiceInstanceMapping.objects.get(client_id=app.pk).instance_id == kept.pk  # the client follows
