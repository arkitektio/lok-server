"""The public vulnerability disclosure pages and security.txt body (RFC 9116, BSI TR-03183-3)."""

import datetime
import re
from collections.abc import Iterator
from typing import Any

import pytest
from django.core import mail
from django.core.cache import cache
from django.test import Client
from pytest_django.fixtures import SettingsWrapper

from cvd.models import VulnerabilityReport
from cvd.securitytxt import render_security_txt
from karakter.models import User
from lok_server.configuration import SecuritySettings

PSIRT_FPR = "D7B9A162DA4BC8711C3C9D46EAF68605EF40FEA6"
CSIRT_FPR = "E904021F2EF95EC71A329A1891EB2A609F055969"
SIGN_FPR = "BA786FE42081B3507CACF30B363EB7A0FBDE0CCD"


def _security(**overrides: Any) -> SecuritySettings:
    data = {
        "vendor": "Example",
        "page_url": "https://go.example.org/security",
        "psirt": {
            "email": "psirt@example.org",
            "key": {
                "url": "https://go.example.org/.well-known/pgp/psirt.asc",
                "fingerprint": PSIRT_FPR,
            },
        },
        "csirt": {
            "email": "csirt@example.org",
            "key": {
                "url": "https://go.example.org/.well-known/pgp/csirt.asc",
                "fingerprint": CSIRT_FPR,
            },
        },
        "signing_key": {
            "url": "https://go.example.org/.well-known/pgp/security-txt.asc",
            "fingerprint": SIGN_FPR,
        },
        "canonical": [
            "https://go.example.org/.well-known/security.txt",
            "https://beta.example.org/.well-known/security.txt",
        ],
        "expires": datetime.date(2027, 11, 15),
        "policy_updated": datetime.date(2026, 11, 16),
        "privacy_policy_url": "https://example.org/privacy",
    }
    data.update(overrides)
    return SecuritySettings.model_validate(data)


@pytest.fixture
def security(settings: SettingsWrapper) -> Iterator[SecuritySettings]:
    cache.clear()
    settings.SECURITY_DISCLOSURE = _security()
    settings.EMAIL_HOST = "smtp.example.org"
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    yield settings.SECURITY_DISCLOSURE
    cache.clear()


def _report(**overrides: Any) -> dict[str, str]:
    data = {
        "kind": "product",
        "title": "Token endpoint leaks timing",
        "affected": "lok 2026.11 /o/token/",
        "description": "Timing differs for valid and invalid client ids.",
        "reproduction": "",
        "contact": "+49 30 1234567",
        "language": "en",
        "website": "",
    }
    data.update(overrides)
    return data


# --- security.txt -----------------------------------------------------------------


def test_security_txt_has_the_bsi_fields_in_order() -> None:
    body = render_security_txt(_security())
    fields = [
        line.split(":", 1)[0]
        for line in body.splitlines()
        if line and not line.startswith("#")
    ]
    assert fields == [
        "Canonical",
        "Canonical",
        "Contact",
        "Contact",
        "Contact",
        "Encryption",
        "Encryption",
        "Acknowledgments",
        "Preferred-Languages",
        "Policy",
        "Expires",
    ]
    contacts = [line for line in body.splitlines() if line.startswith("Contact:")]
    # 4.2.3: PSIRT first, CSIRT second, then the web page with the form.
    assert contacts == [
        "Contact: mailto:psirt@example.org",
        "Contact: mailto:csirt@example.org",
        "Contact: https://go.example.org/security",
    ]
    assert "Expires: 2027-11-15T00:00:00Z" in body
    assert "# Our canonical URI" in body and "# Our security addresses" in body
    assert "CSAF:" not in body


def test_security_txt_fields_are_printable_ascii() -> None:
    body = render_security_txt(
        _security(csaf="https://go.example.org/.well-known/csaf/provider-metadata.json")
    )
    assert (
        "CSAF: https://go.example.org/.well-known/csaf/provider-metadata.json" in body
    )
    for line in body.splitlines():
        if not line.startswith("#"):
            assert all(0x20 <= ord(c) <= 0x7E for c in line)
    with pytest.raises(ValueError):
        render_security_txt(_security(page_url="https://gö.example.org/security"))


def test_english_must_be_a_preferred_language() -> None:
    with pytest.raises(ValueError):
        _security(preferred_languages=["de"])


def test_fingerprints_are_normalized() -> None:
    sec = _security(
        signing_key={
            "url": "https://x/k.asc",
            "fingerprint": "ba78 6fe4 2081 b350 7cac  f30b 363e b7a0 fbde 0ccd",
        }
    )
    assert sec.signing_key.fingerprint == SIGN_FPR


# --- the pages ---------------------------------------------------------------------


@pytest.mark.django_db
def test_pages_are_404_without_config(settings: SettingsWrapper) -> None:
    settings.SECURITY_DISCLOSURE = None
    assert Client().get("/lok/security/").status_code == 404
    assert Client().get("/lok/security/policy").status_code == 404


@pytest.mark.django_db
def test_report_page_carries_everything_without_javascript(
    security: SecuritySettings,
) -> None:
    response = Client().get("/lok/security/")
    assert response.status_code == 200
    html = response.content.decode()
    assert "<script" not in html
    for needle in (
        "psirt@example.org",
        "csirt@example.org",
        "D7B9 A162",
        "E904 021F",
        "BA78 6FE4",
        "psirt.asc",
        "csirt.asc",
        "security-txt.asc",
        "15 November 2027",
        "/security/policy",
        "https://example.org/privacy",
        'method="post"',
        'action="/security"',
    ):
        assert needle in html, needle


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path",
    [
        "/lok/security/policy",
        "/lok/security/policy/",
        "/lok/security/acknowledgements",
        "/lok/security/thanks",
    ],
)
def test_the_other_pages_render(security: SecuritySettings, path: str) -> None:
    response = Client().get(path)
    assert response.status_code == 200
    assert "<script" not in response.content.decode()


@pytest.mark.django_db
def test_policy_shows_its_date_and_vendor(security: SecuritySettings) -> None:
    html = Client().get("/lok/security/policy").content.decode()
    assert "16 November 2026" in html
    assert "Example" in html
    assert (
        "five working days" in html
        and "90 days" in html
        and "anonymous" in html.lower()
    )


@pytest.mark.django_db
def test_an_anonymous_report_is_stored_without_any_trace(
    security: SecuritySettings,
) -> None:
    response = Client(REMOTE_ADDR="203.0.113.9").post(
        "/lok/security/", _report(contact="")
    )
    assert response.status_code == 303
    assert re.match(
        r"^/security/thanks\?ref=VR-[A-Z0-9]{4}-[A-Z0-9]{4}$", response["Location"]
    )
    report = VulnerabilityReport.objects.get()
    assert report.is_anonymous
    stored = " ".join(str(getattr(report, f.attname)) for f in report._meta.fields)
    assert "203.0.113.9" not in stored


@pytest.mark.django_db
def test_psirt_is_notified_without_the_report_content(
    security: SecuritySettings,
) -> None:
    Client().post("/lok/security/", _report())
    report = VulnerabilityReport.objects.get()
    assert report.contact == "+49 30 1234567"  # a phone number is a valid contact
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["psirt@example.org"]  # never the reporter
    assert report.reference in message.subject
    assert "Timing differs" not in message.body


@pytest.mark.django_db
def test_credit_is_only_kept_when_asked(security: SecuritySettings) -> None:
    Client().post("/lok/security/", _report(credit_name="alice"))
    assert VulnerabilityReport.objects.get().credit_name == ""


@pytest.mark.django_db
def test_invalid_report_is_shown_again(security: SecuritySettings) -> None:
    response = Client().post("/lok/security/", _report(title=""))
    assert response.status_code == 400
    assert VulnerabilityReport.objects.count() == 0


@pytest.mark.django_db
def test_the_honeypot_files_nothing(security: SecuritySettings) -> None:
    response = Client().post("/lok/security/", _report(website="http://spam"))
    assert response.status_code in (302, 303)
    assert VulnerabilityReport.objects.count() == 0


@pytest.mark.django_db
def test_bulk_submissions_are_throttled(security: SecuritySettings) -> None:
    client = Client(REMOTE_ADDR="198.51.100.4")
    from cvd.views import REPORT_LIMIT_PER_MINUTE

    codes = [
        client.post("/lok/security/", _report()).status_code
        for _ in range(REPORT_LIMIT_PER_MINUTE + 1)
    ]
    assert codes[:REPORT_LIMIT_PER_MINUTE] == [303] * REPORT_LIMIT_PER_MINUTE
    assert codes[-1] == 429


@pytest.mark.django_db
def test_thanks_page_ignores_a_forged_reference(security: SecuritySettings) -> None:
    html = Client().get("/lok/security/thanks?ref=<b>x</b>").content.decode()
    assert "<b>x</b>" not in html and "Your reference" not in html


# --- two-person close (4.4.7g) -----------------------------------------------------


@pytest.mark.django_db
def test_one_analyst_cannot_close_a_report_alone(django_user_model: type[User]) -> None:
    a = django_user_model.objects.create(username="a")
    b = django_user_model.objects.create(username="b")
    report = VulnerabilityReport.objects.create(
        title="t", affected="x", description="d", status="triage"
    )

    assert report.request_status("closed", a) == "proposed"
    assert report.status == "triage"
    # The same analyst saving again only renews the proposal.
    assert report.request_status("closed", a) == "proposed"
    assert report.status == "triage"
    # A second analyst asking for a *different* closing status starts a new proposal.
    assert report.request_status("rejected", b) == "proposed"
    assert report.status == "triage" and report.close_proposed_by == b
    assert report.request_status("rejected", a) == "confirmed"
    assert report.status == "rejected" and report.close_confirmed_by == a


@pytest.mark.django_db
def test_admin_binds_the_close_to_the_logged_in_user(
    django_user_model: type[User],
) -> None:
    from django.contrib.admin.sites import site
    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.test import RequestFactory

    admin_ = site._registry[VulnerabilityReport]
    a = django_user_model.objects.create(username="a", is_staff=True, is_superuser=True)
    b = django_user_model.objects.create(username="b", is_staff=True, is_superuser=True)
    report = VulnerabilityReport.objects.create(
        title="t", affected="x", description="d"
    )

    def save_as(user: User, status: str) -> VulnerabilityReport:
        request = RequestFactory().post("/")
        request.user = user
        request.session = {}
        request._messages = FallbackStorage(request)
        obj = VulnerabilityReport.objects.get(pk=report.pk)
        obj.status = status
        admin_.save_model(request, obj, form=None, change=True)
        return VulnerabilityReport.objects.get(pk=report.pk)

    assert save_as(a, "closed").status == "new"
    assert save_as(a, "closed").status == "new"
    final = save_as(b, "closed")
    assert final.status == "closed"
    assert (final.close_proposed_by, final.close_confirmed_by) == (a, b)
