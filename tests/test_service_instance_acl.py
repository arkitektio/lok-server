"""`updateServiceInstance` rewrites who gets a service instance at app approval
(fakts.services.rendering.find_instance_for_requirement_and_hub): an owner/admin
operation, not something any member's app may do."""

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from fakts.models import ServiceInstance
from karakter.models import User
from lok_server.schema import schema as main_schema
from tests import factories
from tests.conftest import build_auth_context

UPDATE = """
mutation ($id: ID!, $denied: [ID!]) {
  updateServiceInstance(input: {id: $id, deniedUsers: $denied}) { id }
}
"""


def _setup(as_owner: bool) -> tuple[HttpContext, ServiceInstance, User]:
    organization = factories.make_organization()
    factories.make_membership(user=organization.owner, organization=organization)
    instance = factories.make_service_instance(
        hub=factories.make_hub(organization=organization)
    )
    member = factories.make_membership(organization=organization)
    instance.denied_users.add(member.user)
    actor = organization.owner if as_owner else member.user
    membership = organization.memberships.get(user=actor)
    context = build_auth_context(
        actor,
        organization,
        factories.make_client(membership=membership),
        scope="openid lok_all",
    )
    return context, instance, member.user


@sync_to_async
def _denied_ids(instance: ServiceInstance) -> set[int]:
    return set(instance.denied_users.values_list("id", flat=True))


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_plain_member_cannot_lift_their_own_deny() -> None:
    context, instance, member = await sync_to_async(_setup)(as_owner=False)

    result = await main_schema.execute(
        UPDATE,
        variable_values={"id": str(instance.pk), "denied": []},
        context_value=context,
    )

    assert result.errors
    assert await _denied_ids(instance) == {member.pk}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_owner_rewrites_the_acl() -> None:
    context, instance, _member = await sync_to_async(_setup)(as_owner=True)

    result = await main_schema.execute(
        UPDATE,
        variable_values={"id": str(instance.pk), "denied": []},
        context_value=context,
    )

    assert result.errors is None, result.errors
    assert await _denied_ids(instance) == set()
