"""A hub's TLS certificate: sent in its manifest, stored on accept, handed to apps on each instance.

A hub that registers no certificate stays a plain-HTTP hub (no ``tls`` on its instances); one
authorized again without it becomes one again; a new certificate replaces the old one.
"""

import datetime
import ipaddress

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from fakts import base_models
from fakts.services.hub_tls import validate_tls_certificate
from tests import factories
from tests.test_instance_keys import _enroll, _instance


def _certificate(*, ca: bool = False, san: bool = True) -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "lab.local")])
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=3650)).add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    if san:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName("lab.local"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
    return builder.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode()


def _context() -> base_models.LinkingContext:
    return base_models.LinkingContext(
        request=base_models.LinkingRequest(host="lab.local", port="443", is_secure=True),
        manifest=base_models.Manifest(identifier="com.example.app", version="1.0.0"),
        client=base_models.LinkingClient(
            authorization_grant_type="authorization_code",
            client_type="confidential",
            client_id="cid",
            client_secret="secret",
            name="client",
        ),
    )


def _enroll_tls(client, admin, certificate, uid="0192f3c4-1111-7000-8000-000000000001"):
    """`_enroll` with a hub-level `tls_certificate` (the manifest field sits next to `instances`)."""
    import json

    from django.urls import reverse
    from types import SimpleNamespace

    from api.management.mutations.hub_device_code import AcceptHubDeviceCodeInput, accept_hub_device_code
    from fakts import models
    from tests.test_fakts_flows import _poll

    hub_manifest = {"identifier": "tlshub", "uid": uid, "instances": [_instance("mikro", None)], "clients": []}
    if certificate is not None:
        hub_manifest["tls_certificate"] = certificate
    body = client.post(reverse("hub_authorization"), data=json.dumps({"hub": hub_manifest}), content_type="application/json").json()
    code = models.DeviceCode.objects.get(secret=body["device_code"])
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=admin.user)))
    hub = accept_hub_device_code(
        info,
        AcceptHubDeviceCodeInput(device_code=str(code.id), code=code.code, organization=str(admin.organization.id), allow_ionscale=False),
    )
    assert _poll(client, body["device_code"], body["client_id"]).status_code == 200
    return hub


@pytest.fixture
def admin():
    org = factories.make_organization()
    return factories.make_membership(user=org.owner, organization=org)


@pytest.mark.django_db
def test_a_registered_certificate_is_what_apps_verify_the_instances_by(client, admin):
    certificate = _certificate()
    hub = _enroll_tls(client, admin, certificate)

    claim = hub.instances.get().render(_context())
    assert claim.tls is not None
    assert claim.tls.certificates == [validate_tls_certificate(certificate)]


@pytest.mark.django_db
def test_a_hub_without_a_certificate_stays_plain_http(client, admin):
    hub, _ = _enroll(client, admin, [_instance("mikro", None)])
    assert hub.tls_certificate is None
    assert hub.instances.get().render(_context()).tls is None


@pytest.mark.django_db
def test_reauthorizing_replaces_or_drops_the_certificate(client, admin):
    first, second = _certificate(), _certificate()
    _enroll_tls(client, admin, first)
    hub = _enroll_tls(client, admin, second)
    hub.refresh_from_db()
    assert hub.tls_certificate == validate_tls_certificate(second)

    hub = _enroll_tls(client, admin, None)
    hub.refresh_from_db()
    assert hub.tls_certificate is None
    assert hub.instances.get().render(_context()).tls is None


@pytest.mark.parametrize(
    ("certificate", "match"),
    [
        ("not a pem", "exactly one"),
        (_certificate() + _certificate(), "exactly one"),
        (_certificate(ca=True), "not a CA"),
        (_certificate(san=False), "subjectAltName"),
    ],
)
def test_a_certificate_that_is_not_one_server_certificate_is_refused(certificate, match):
    with pytest.raises(ValueError, match=match):
        validate_tls_certificate(certificate)
