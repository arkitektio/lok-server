"""lok's own probe of public aliases (``fakts.services.alias_probe``).

A hosted hub that never calls ``/f/hubhealth/`` is still shown reachable when its
public aliases answer a fresh nonce with their instance key's signature — and only
then: a plain 200, a signature of another key, or an instance without a key never
counts. Public aliases are https-only.
"""

import base64
from datetime import timedelta

import pytest
from asgiref.sync import sync_to_async
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from django.db import IntegrityError
from django.utils import timezone
from pydantic import ValidationError

from api.management.schema import schema as management_schema
from fakts import base_models, models
from fakts.services import alias_probe
from fakts.services.alias_probe import CHALLENGE_DOMAIN, Fetched, challenge_url, fetch_challenge, probe_aliases, probe_hub
from tests import factories
from tests.conftest import build_auth_context


def _keypair():
    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    return private, public


def _sign(private, nonce):
    return base64.b64encode(private.sign(f"{CHALLENGE_DOMAIN}:{nonce}".encode())).decode()


def _signing(private, calls=None):
    """A fetcher whose far end signs every nonce with ``private``."""

    def fetch(url, nonce, ca_bundle):
        if calls is not None:
            calls.append((url, ca_bundle))
        return Fetched(http_status=200, signature=_sign(private, nonce), latency_ms=12)

    return fetch


def _public_alias(hub=None, *, public_key=None, host="lab.example.org", **kw):
    instance = factories.make_service_instance(hub=hub, public_key=public_key)
    kw.setdefault("port", None)
    return models.InstanceAlias.objects.create(
        instance=instance, host=host, path="mikro", challenge="ht", ssl=True, public=True, kind="absolute", scope="public", **kw
    )


def test_challenge_url_is_the_alias_path_then_its_challenge():
    alias = models.InstanceAlias(host="lab.example.org", port=8443, path="/mikro/", challenge="ht")
    assert challenge_url(alias) == "https://lab.example.org:8443/mikro/ht"
    assert challenge_url(models.InstanceAlias(host="::1", path=None, challenge="ht")) == "https://[::1]/ht"


@pytest.mark.django_db
def test_a_valid_signature_verifies_and_makes_the_hub_reachable():
    private, public = _keypair()
    alias = _public_alias(public_key=public)
    hub = alias.instance.hub
    assert not hub.reachable

    [result] = probe_aliases(fetch=_signing(private))

    alias.refresh_from_db()
    assert result.status == alias.probe_status == "verified"
    assert alias.last_verified_at is not None and alias.probe_http_status == 200 and alias.probe_latency_ms == 12
    assert hub.reachable
    assert not hub.online  # never reported itself: reachability is lok's own view


@pytest.mark.django_db
def test_a_signature_of_another_key_is_not_proof():
    _, public = _keypair()
    impostor, _ = _keypair()
    alias = _public_alias(public_key=public)

    probe_aliases(fetch=_signing(impostor))

    alias.refresh_from_db()
    assert alias.probe_status == "bad_signature"
    assert alias.last_verified_at is None
    assert not alias.instance.hub.reachable


@pytest.mark.django_db
def test_a_plain_200_is_not_proof():
    _, public = _keypair()
    alias = _public_alias(public_key=public)

    probe_aliases(fetch=lambda url, nonce, ca: Fetched(error="unsigned", http_status=200, latency_ms=3))

    alias.refresh_from_db()
    assert alias.probe_status == "unsigned"
    assert not alias.instance.hub.reachable


@pytest.mark.django_db
def test_an_instance_without_a_key_cannot_be_proven():
    signer, _ = _keypair()
    alias = _public_alias(public_key=None)

    probe_aliases(fetch=_signing(signer))

    alias.refresh_from_db()
    assert alias.probe_status == "no_key"
    assert not alias.instance.hub.reachable


@pytest.mark.django_db
def test_a_signature_for_a_different_nonce_is_a_replay():
    private, public = _keypair()
    alias = _public_alias(public_key=public)
    replayed = _sign(private, "an-old-nonce")

    probe_aliases(fetch=lambda url, nonce, ca: Fetched(http_status=200, signature=replayed))

    alias.refresh_from_db()
    assert alias.probe_status == "bad_signature"


@pytest.mark.django_db
def test_one_url_is_fetched_once_and_judged_against_each_rows_key():
    """A partner hub repeats its aliases in every organization: one request, but a stale
    key in one organization does not ride on another's verified answer."""
    private, public = _keypair()
    _, stale = _keypair()
    good = _public_alias(public_key=public)
    other = _public_alias(public_key=stale)
    calls = []

    probe_aliases(fetch=_signing(private, calls))

    assert len(calls) == 1
    good.refresh_from_db()
    other.refresh_from_db()
    assert (good.probe_status, other.probe_status) == ("verified", "bad_signature")


@pytest.mark.django_db
def test_a_hub_with_its_own_certificate_is_trusted_on_exactly_it():
    private, public = _keypair()
    hub = factories.make_hub(tls_certificate="-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n")
    _public_alias(hub, public_key=public)
    calls = []

    probe_aliases(fetch=_signing(private, calls))

    [(_url, ca_bundle)] = calls
    assert ca_bundle and ca_bundle.endswith(".pem")


@pytest.mark.django_db
def test_only_public_https_absolute_aliases_are_probed():
    private, public = _keypair()
    instance = factories.make_service_instance(public_key=public)
    models.InstanceAlias.objects.create(instance=instance, host="lab.example.org", path="a", challenge="ht", ssl=True, public=False)
    models.InstanceAlias.objects.create(instance=instance, kind="mesh", path="b", challenge="ht", ssl=True, public=True)
    calls = []

    assert probe_aliases(fetch=_signing(private, calls)) == []
    assert calls == []


@pytest.mark.django_db
def test_a_manual_recheck_moments_after_a_probe_does_not_refetch():
    private, public = _keypair()
    alias = _public_alias(public_key=public)
    calls = []

    probe_hub(alias.instance.hub, fetch=_signing(private, calls))
    probe_hub(alias.instance.hub, fetch=_signing(private, calls))
    assert len(calls) == 1

    models.InstanceAlias.objects.filter(pk=alias.pk).update(probed_at=timezone.now() - timedelta(minutes=1))
    probe_hub(alias.instance.hub, fetch=_signing(private, calls))
    assert len(calls) == 2


@pytest.mark.django_db
def test_reachability_lapses_after_three_probe_intervals(settings):
    private, public = _keypair()
    alias = _public_alias(public_key=public)
    probe_aliases(fetch=_signing(private))
    models.InstanceAlias.objects.filter(pk=alias.pk).update(
        last_verified_at=timezone.now() - timedelta(seconds=3 * settings.HUB_PROBE_INTERVAL + 1)
    )
    assert not alias.instance.hub.reachable


# --- the request itself ------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code=200, body=b""):
        self.status_code = status_code
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size=1024):
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start : start + chunk_size]


@pytest.mark.parametrize(
    "status, body, expected",
    [
        (200, b'{"signature": "abc"}', Fetched(http_status=200, signature="abc")),
        (200, b"<html>System status</html>", Fetched(error="unsigned", http_status=200)),
        (200, b'{"status": "ok"}', Fetched(error="unsigned", http_status=200)),
        (200, b"x" * 10_000, Fetched(error="unsigned", http_status=200)),
        (503, b"down", Fetched(error="unhealthy", http_status=503)),
        (302, b"", Fetched(error="unhealthy", http_status=302)),
    ],
)
def test_fetch_reads_a_signed_answer_and_nothing_else(monkeypatch, status, body, expected):
    seen = {}

    def fake_get(url, **kwargs):
        seen.update(kwargs)
        return _FakeResponse(status, body)

    monkeypatch.setattr(alias_probe.requests, "get", fake_get)
    fetched = fetch_challenge("https://lab.example.org/mikro/ht", "n0nce", guard=False)

    assert (fetched.error, fetched.http_status, fetched.signature) == (expected.error, expected.http_status, expected.signature)
    assert seen["params"] == {"nonce": "n0nce"}
    assert seen["allow_redirects"] is False and seen["verify"] is True


def test_fetch_refuses_hosts_that_are_not_public(monkeypatch):
    monkeypatch.setattr(alias_probe.requests, "get", lambda *a, **k: pytest.fail("must not be requested"))
    assert fetch_challenge("https://localhost/mikro/ht", "n").error == "non_public_address"
    assert fetch_challenge("http://lab.example.org/mikro/ht", "n").error == "non_public_address"


# --- https only --------------------------------------------------------------------------


def test_a_staged_public_alias_must_be_https():
    with pytest.raises(ValidationError, match="https"):
        base_models.StagingAlias(id="a", host="lab.example.org", reach="public", lok_checks=True, ssl=False)
    assert base_models.StagingAlias(id="a", host="lab.example.org", reach="public", lok_checks=True).public


@pytest.mark.django_db
def test_the_database_refuses_a_plain_http_public_alias():
    instance = factories.make_service_instance()
    with pytest.raises(IntegrityError):
        models.InstanceAlias.objects.create(instance=instance, host="lab.example.org", challenge="ht", ssl=False, public=True)


# --- the mutation ------------------------------------------------------------------------

PROBE_HUB = """
mutation ($id: ID!) {
  probeHub(input: { id: $id }) {
    id reachable lastVerifiedAt
    publicAliases { id probeStatus probeHttpStatus probeLatencyMs lastVerifiedAt }
    instances { aliases { probeStatus probeHttpStatus lastVerifiedAt } }
  }
}
"""


def _setup_hub_with_admin_and_member():
    private, public = _keypair()
    alias = _public_alias(public_key=public)
    hub = alias.instance.hub
    admin = factories.make_membership(user=hub.organization.owner, organization=hub.organization)
    member = factories.make_membership(organization=hub.organization)
    contexts = {
        name: build_auth_context(m.user, m.organization, factories.make_client(membership=m)) for name, m in (("admin", admin), ("member", member))
    }
    return private, hub, contexts


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_admin_probes_a_hub_now(monkeypatch):
    private, hub, contexts = await sync_to_async(_setup_hub_with_admin_and_member)()
    monkeypatch.setattr(alias_probe, "fetch_challenge", _signing(private))

    result = await management_schema.execute(PROBE_HUB, context_value=contexts["admin"], variable_values={"id": str(hub.id)})

    assert not result.errors, result.errors
    data = result.data["probeHub"]
    assert data["reachable"] is True and data["lastVerifiedAt"]
    assert data["instances"][0]["aliases"][0]["probeStatus"] == "verified"
    [public] = data["publicAliases"]
    assert public["probeStatus"] == "verified" and public["probeHttpStatus"] == 200


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_plain_member_cannot_trigger_a_probe(monkeypatch):
    private, hub, contexts = await sync_to_async(_setup_hub_with_admin_and_member)()
    monkeypatch.setattr(alias_probe, "fetch_challenge", lambda *a: pytest.fail("must not probe"))

    result = await management_schema.execute(PROBE_HUB, context_value=contexts["member"], variable_values={"id": str(hub.id)})

    assert result.errors


# --- the partner path, end to end --------------------------------------------------------


def _partner(public_key, *, lok_checks=True):
    return models.KommunityPartner.objects.create(
        name="Demo",
        identifier="demo-probe",
        partner_kind="preauthorized",
        kommunity_kind="open",
        preconfigured_hub={
            "identifier": "demo.example.org",
            "instances": [
                {
                    "identifier": "Rekuest",
                    "manifest": {"identifier": "live.arkitekt.rekuest", "version": "1.0.0", "challenge_key": public_key},
                    "aliases": [
                        {"id": "public", "host": "demo.example.org", "path": "rekuest", "challenge": "ht", "reach": "public", "ssl": True, "lok_checks": lok_checks}
                    ],
                }
            ],
        },
    )


@pytest.mark.django_db
def test_a_partner_hub_in_two_organizations_is_verified_with_one_request():
    """Configured from lok's config, the partner hub never reports itself: lok's probe of
    its `lok_checks` alias, signed with the manifest's `challenge_key`, makes it reachable
    in every organization that connected it."""
    from fakts.services.hubs import create_hub_from_partner

    private, public = _keypair()
    partner = _partner(public)
    hubs = []
    for _ in range(2):
        organization = factories.make_organization()
        factories.make_membership(user=organization.owner, organization=organization)
        hubs.append(create_hub_from_partner(partner=partner, organization=organization))
    calls = []

    results = probe_aliases(fetch=_signing(private, calls))

    assert [url for url, _ca in calls] == ["https://demo.example.org/rekuest/ht"]
    assert [result.status for result in results] == ["verified", "verified"]
    assert all(hub.reachable and not hub.online for hub in hubs)


@pytest.mark.django_db
def test_a_partner_alias_without_lok_checks_is_not_probed():
    from fakts.services.hubs import create_hub_from_partner

    private, public = _keypair()
    organization = factories.make_organization()
    factories.make_membership(user=organization.owner, organization=organization)
    create_hub_from_partner(partner=_partner(public, lok_checks=False), organization=organization)

    assert probe_aliases(fetch=_signing(private)) == []
