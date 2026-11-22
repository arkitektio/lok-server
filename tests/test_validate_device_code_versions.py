"""The connect flow says, per requirement, whether the hub's instance runs a version the app wants.

Advisory, like the claim's ``satisfies``: an instance outside the range is still mapped.
"""

import pytest
from asgiref.sync import sync_to_async

from api.management.schema import schema as management_schema
from tests import factories
from tests.conftest import build_auth_context

VALIDATE = """
    query ($dc: ID!, $hub: ID!, $code: String!) {
        validateDeviceCode(deviceCode: $dc, hub: $hub, code: $code) {
            valid
            mappings { key wantsVersion satisfied serviceInstance { release { version } } }
        }
    }
"""

DEVICE_CODE = """
    query ($code: String!) {
        deviceCodeByCode(deviceCode: $code) { stagingManifest { requirements { key version } } }
    }
"""


def _setup():
    membership = factories.make_membership()
    organization = membership.organization
    context = build_auth_context(
        membership.user, organization, factories.make_client(membership=membership)
    )
    hub = factories.make_hub(organization=organization)
    for identifier, version in (
        ("live.arkitekt.rekuest", "12.2.0"),
        ("live.arkitekt.mikro", "11.0.0"),
        ("live.arkitekt.kabinet", "0+local"),
    ):
        service = factories.make_service(
            organization=organization, identifier=identifier
        )
        factories.make_service_instance(
            hub=hub,
            release=factories.make_service_release(service=service, version=version),
        )
    device_code = factories.make_device_code(
        staging_manifest={
            "identifier": "com.example.app",
            "version": "1.0.0",
            "scopes": [],
            "requirements": [
                {
                    "key": "rekuest",
                    "service": "live.arkitekt.rekuest",
                    "version": ">=12.1,<14",
                },
                {"key": "mikro", "service": "live.arkitekt.mikro", "version": ">=12"},
                {
                    "key": "kabinet",
                    "service": "live.arkitekt.kabinet",
                    "version": ">=1",
                },
                {"key": "any", "service": "live.arkitekt.rekuest"},
            ],
        }
    )
    return context, hub, device_code


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_validate_device_code_judges_versions():
    context, hub, device_code = await sync_to_async(_setup)()

    result = await management_schema.execute(
        VALIDATE,
        context_value=context,
        variable_values={
            "dc": str(device_code.id),
            "hub": str(hub.id),
            "code": device_code.code,
        },
    )
    assert not result.errors, result.errors
    data = result.data["validateDeviceCode"]
    # Advisory: a version outside the range does not make the request invalid.
    assert data["valid"] is True
    by_key = {m["key"]: m for m in data["mappings"]}
    assert (by_key["rekuest"]["wantsVersion"], by_key["rekuest"]["satisfied"]) == (
        ">=12.1,<14",
        True,
    )
    assert (by_key["mikro"]["wantsVersion"], by_key["mikro"]["satisfied"]) == (
        ">=12",
        False,
    )
    assert by_key["mikro"]["serviceInstance"]["release"]["version"] == "11.0.0"
    # A local build is of no known release: nothing to say.
    assert by_key["kabinet"]["satisfied"] is None
    assert (by_key["any"]["wantsVersion"], by_key["any"]["satisfied"]) == (None, None)

    result = await management_schema.execute(
        DEVICE_CODE, context_value=context, variable_values={"code": device_code.code}
    )
    assert not result.errors, result.errors
    versions = {
        r["key"]: r["version"]
        for r in result.data["deviceCodeByCode"]["stagingManifest"]["requirements"]
    }
    assert versions == {
        "rekuest": ">=12.1,<14",
        "mikro": ">=12",
        "kabinet": ">=1",
        "any": None,
    }
