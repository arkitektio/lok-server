"""Validate the lok service's config.yaml against its bespoke schema.

Standalone — needs no database; run with ``uv run pytest tests/test_config.py``.
"""

import pytest
from pydantic import ValidationError

from lok_server.configuration import AccountSettings, Settings


def test_config_yaml_validates():
    """The service's own config.yaml parses into the typed schema."""
    s = Settings()
    assert s.postgres.db_name
    assert s.redis.host


def test_env_override(monkeypatch):
    """Env vars override the YAML file (nested via ``__``)."""
    monkeypatch.setenv("POSTGRES__PASSWORD", "from-env-test")
    assert Settings().postgres.password == "from-env-test"


def test_signup_fields_derived_from_login_methods():
    """When signup_fields is omitted it is derived from login_methods."""
    assert AccountSettings().signup_fields == ["username*", "password1*", "password2*"]
    assert AccountSettings(login_methods=["email"]).signup_fields == [
        "email*",
        "password1*",
        "password2*",
    ]


def test_email_login_requires_email_signup_field():
    """Email login paired with a signup form that never collects an email fails."""
    with pytest.raises(ValidationError, match="email"):
        AccountSettings(login_methods=["email"], signup_fields=["username*", "password1*", "password2*"])


def test_mandatory_verification_requires_smtp_block():
    """Mandatory email verification without an `email:` SMTP block fails fast."""
    with pytest.raises(ValidationError, match="mandatory"):
        Settings(account={"email_verification": "mandatory"})


def test_social_provider_config_dumps_to_allauth_shape():
    """A typed provider entry round-trips to exactly the dict allauth expects,
    preserving provider-specific extras and dropping unset optionals."""
    s = Settings(socialaccount_providers={
        "google": {
            "APP": {"client_id": "gid", "secret": "gsecret"},
            "SCOPE": ["profile", "email"],
            "FETCH_USERINFO": True,  # provider-specific extra
        }
    })
    dumped = {p: c.model_dump(exclude_none=True) for p, c in s.socialaccount_providers.items()}
    assert dumped == {
        "google": {
            "APP": {"client_id": "gid", "secret": "gsecret", "key": "", "settings": {}},
            "SCOPE": ["profile", "email"],
            "FETCH_USERINFO": True,
        }
    }


def test_social_provider_rejects_bad_app_credentials():
    """A missing/mistyped APP credential key is caught at load time."""
    with pytest.raises(ValidationError):
        Settings(socialaccount_providers={"google": {"APP": {"clientid": "typo"}}})


def _saml_app(client_id="acme-university", trusted=True):
    """A SAML APPS entry of the shape allauth's `list_apps` reads.

    `client_id` is an identity-provider name and the URL segment — it carries no
    organization meaning.
    """
    settings = {
        "attribute_mapping": {"uid": ["urn:oasis:names:tc:SAML:attribute:subject-id"]},
        "idp": {"entity_id": f"https://idp.{client_id}.edu/idp", "sso_url": "https://idp/sso"},
    }
    if trusted:
        settings["verified_email"] = [f"{client_id}.edu"]
    return {"client_id": client_id, "provider_id": f"saml:{client_id}", "name": client_id, "settings": settings}


def test_saml_app_validates_without_oauth_credentials():
    """A SAML app carries no OAuth secret/key, and the `extra="forbid"` app model
    accepts it unchanged — this is why SAML needs no config-schema change."""
    s = Settings(socialaccount_providers={"saml": {"APPS": [_saml_app()]}})
    app = s.socialaccount_providers["saml"].APPS[0]
    assert app.client_id == "acme-university"
    assert app.provider_id == "saml:acme-university"
    assert app.secret == "" and app.key == ""
    # The settings blob is passed through to allauth verbatim.
    assert app.settings["idp"]["sso_url"] == "https://idp/sso"
    assert app.settings["verified_email"] == ["acme-university.edu"]


def test_saml_provider_dumps_to_allauth_shape():
    """The typed SAML entry round-trips to the dict allauth expects."""
    s = Settings(socialaccount_providers={"saml": {"APPS": [_saml_app()]}})
    dumped = {p: c.model_dump(exclude_none=True) for p, c in s.socialaccount_providers.items()}
    assert dumped["saml"]["APPS"] == [
        {
            "client_id": "acme-university",
            "provider_id": "saml:acme-university",
            "name": "acme-university",
            "secret": "",
            "key": "",
            "settings": _saml_app()["settings"],
        }
    ]


def test_saml_app_rejects_unknown_top_level_key():
    """Misplacing a SAML setting at app level (instead of under `settings`) is caught."""
    with pytest.raises(ValidationError):
        Settings(socialaccount_providers={"saml": {"APPS": [{"client_id": "acme", "idp": {}}]}})


def _relaxed(**providers):
    return dict(
        account={"email_verification": "none", "social_email_verification": "none"},
        socialaccount_providers=providers,
    )


def test_per_app_domain_list_satisfies_trust_requirement():
    """Domain-scoped per-app trust is the accepted form for a multi-app provider."""
    s = Settings(**_relaxed(saml={"APPS": [_saml_app("acme"), _saml_app("beta")]}))
    assert s.socialaccount_providers["saml"].APPS[0].settings["verified_email"] == ["acme.edu"]


def test_untrusted_saml_app_is_rejected_when_verification_relaxed():
    """One untrusted app among several is caught, not silently exempted."""
    with pytest.raises(ValidationError, match="not marked trusted"):
        Settings(**_relaxed(saml={"APPS": [_saml_app("acme", trusted=False), _saml_app("beta", trusted=False)]}))


def test_multi_app_provider_cannot_declare_trust_with_one_flat_flag():
    """A provider-wide VERIFIED_EMAIL cannot express per-IdP trust."""
    with pytest.raises(ValidationError, match="list of domains"):
        Settings(
            **_relaxed(
                saml={
                    "VERIFIED_EMAIL": True,
                    "APPS": [_saml_app("acme", trusted=False), _saml_app("beta", trusted=False)],
                }
            )
        )


def test_multi_app_provider_cannot_declare_trust_with_bare_true():
    """The hole this rule closes: `verified_email: true` is truthy, so it used to pass
    the per-app check while trusting the IdP about *any* address — including domains it
    does not own. With EMAIL_AUTHENTICATION that was an account-takeover vector."""
    app = _saml_app("acme", trusted=False)
    app["settings"]["verified_email"] = True

    with pytest.raises(ValidationError, match="does not own"):
        Settings(**_relaxed(saml={"APPS": [app, _saml_app("beta")]}))


def test_multi_app_provider_rejects_an_empty_domain_list():
    """An empty list trusts nothing and is far more likely a mistake than intent."""
    app = _saml_app("acme", trusted=False)
    app["settings"]["verified_email"] = []

    with pytest.raises(ValidationError, match="list of domains"):
        Settings(**_relaxed(saml={"APPS": [app, _saml_app("beta")]}))


def test_single_app_saml_provider_also_needs_a_domain_list():
    """SAML needs domain scoping at *any* app count, not just when several are
    configured. An institution's IdP has no business asserting addresses outside its
    own domains, and that is true whether or not a second IdP exists."""
    app = _saml_app("acme", trusted=False)
    app["settings"]["verified_email"] = True

    with pytest.raises(ValidationError, match="does not own"):
        Settings(**_relaxed(saml={"APPS": [app]}))


def test_single_app_oauth_provider_may_still_use_a_bare_flag():
    """The stricter rule must not disturb google/orcid/cilogon, which are single
    well-known providers declaring trust with one provider-wide VERIFIED_EMAIL."""
    Settings(
        **_relaxed(
            google={"VERIFIED_EMAIL": True, "APPS": [{"client_id": "gid", "secret": "s"}]},
            orcid={"VERIFIED_EMAIL": True, "APPS": [{"client_id": "oid", "secret": "s"}]},
        )
    )


def test_single_app_provider_may_still_use_flat_verified_email():
    """Regression guard for the deployed google/orcid/cilogon shape, which declares
    trust with one top-level VERIFIED_EMAIL per single-app provider."""
    s = Settings(
        account={"email_verification": "none", "social_email_verification": "none"},
        socialaccount_providers={
            "google": {"VERIFIED_EMAIL": True, "APPS": [{"client_id": "gid", "secret": "s"}]},
            "orcid": {"VERIFIED_EMAIL": True, "APPS": [{"client_id": "oid", "secret": "s"}]},
        },
    )
    assert s.account.social_email_verification == "none"


def test_privacy_guards_defaults_to_opt_in():
    """Integrated-widget policy defaults to the current consent-prompt behavior."""
    assert Settings().privacy_guards == "opt-in"


def test_privacy_guards_accepts_known_policies():
    """The three supported policies validate."""
    for policy in ("strict", "opt-in", "disabled"):
        assert Settings(privacy_guards=policy).privacy_guards == policy


def test_privacy_guards_rejects_unknown_policy():
    """An unrecognized policy value is caught at load time (typed Literal)."""
    with pytest.raises(ValidationError):
        Settings(privacy_guards="loose")


def test_allow_insecure_transport_defaults_false():
    """OAuth endpoints demand https unless a deployment opts out explicitly."""
    assert Settings().django.allow_insecure_transport is False


def test_allow_insecure_transport_env_override(monkeypatch):
    """The opt-out is a typed config key, overridable via DJANGO__ALLOW_INSECURE_TRANSPORT."""
    monkeypatch.setenv("DJANGO__ALLOW_INSECURE_TRANSPORT", "true")
    assert Settings().django.allow_insecure_transport is True


def _ionscale(**kw):
    from lok_server.configuration import IonscaleSettings

    return IonscaleSettings(server_url="https://mesh.example.org", coord_url="https://mesh.example.org", **kw)


def test_ionscale_service_token_is_enough():
    s = _ionscale(service_token="svc_" + "x" * 40)
    assert s.admin_key is None
    assert s.verify_tls is True and s.timeout == 10.0


def test_ionscale_legacy_admin_key_still_accepted():
    assert _ionscale(admin_key="legacy").admin_key == "legacy"


def test_ionscale_requires_a_credential():
    with pytest.raises(ValidationError, match="service_token"):
        _ionscale()


def test_ionscale_repository_hook_needs_no_credential():
    """Test/fake repositories are wired by dotted path and carry no secret."""
    assert _ionscale(repository="ionscale.testing.FakeIonscaleRepository").service_token is None
