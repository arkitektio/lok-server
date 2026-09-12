"""Lightweight object factories for the lok test suite.

Implemented as plain helper functions (no factory-boy dependency, which cannot
be installed in the build environment). Each ``make_*`` helper fills in sensible,
internally-consistent defaults and accepts keyword overrides, so tests don't have
to hand-wire every FK.

Relationships are kept consistent by default (e.g. a ``Client``'s
``user``/``organization`` derive from its membership).
"""

import itertools
from datetime import timedelta
from uuid import uuid4

from django.utils import timezone

from karakter.models import Organization, User, Membership
from fakts import models as fmodels
from fakts.models import generate_client_id, generate_client_secret
from fakts.enums import ClientKindChoices, ClientRoleChoices

_seq = itertools.count(1)


def _n() -> int:
    return next(_seq)


def make_user(**kw) -> User:
    kw.setdefault("username", f"user{_n()}")
    kw.setdefault("email", f"{kw['username']}@example.com")
    return User.objects.create(**kw)


def make_organization(owner: User | None = None, **kw) -> Organization:
    if owner is None:
        owner = make_user()
    kw.setdefault("slug", f"org{_n()}")
    kw.setdefault("name", kw["slug"].title())
    return Organization.objects.create(owner=owner, **kw)


def make_membership(user: User | None = None, organization: Organization | None = None, **kw) -> Membership:
    if user is None:
        user = make_user()
    if organization is None:
        organization = make_organization()
    membership, _ = Membership.objects.get_or_create(user=user, organization=organization, defaults=kw)
    return membership


def make_oauth2_client(membership: Membership | None = None, **kw) -> fmodels.Client:
    """An identity-only unified Client (what OAuth2Client used to be) — used
    for relying-party-style rows and as the identity half of make_client."""
    if membership is None:
        membership = make_membership()
    kw.setdefault("client_id", generate_client_id())
    kw.setdefault("client_secret", generate_client_secret())
    kw.setdefault("token_endpoint_auth_method", "client_secret_post")
    kw.setdefault("grant_types", "authorization_code refresh_token client_credentials urn:ietf:params:oauth:grant-type:device_code urn:fakts:grant-type:redeem")
    kw.setdefault("scope", "openid profile email")
    kw.setdefault("organization", membership.organization)
    return fmodels.Client.objects.create(membership=membership, **kw)


def make_app(organization: Organization | None = None, **kw) -> fmodels.App:
    n = _n()
    kw.setdefault("name", f"App {n}")
    kw.setdefault("identifier", f"com.example.app{n}")
    kw.setdefault("organization", organization or make_organization())
    return fmodels.App.objects.create(**kw)


def make_release(app: fmodels.App | None = None, **kw) -> fmodels.Release:
    if app is None:
        app = make_app()
    kw.setdefault("version", "1.0.0")
    kw.setdefault("name", f"{app.name} {kw['version']}")
    return fmodels.Release.objects.create(app=app, **kw)


def make_client(membership: Membership | None = None, release: fmodels.Release | None = None, **kw) -> fmodels.Client:
    """A bound unified client (identity + app side in one row)."""
    if membership is None:
        membership = make_membership()
    if release is None:
        release = make_release()
    # Accept and ignore the pre-unification override shape where the identity
    # was a separate row: tests that built one pass it through `oauth2_client`.
    identity = kw.pop("oauth2_client", None)
    if identity is not None:
        identity.release = release
        identity.membership = membership
        identity.organization = kw.pop("organization", membership.organization)
        identity.kind = kw.pop("kind", ClientKindChoices.DEVELOPMENT.value)
        identity.role = kw.pop("role", ClientRoleChoices.INTERFACE.value)
        for key, value in kw.items():
            setattr(identity, key, value)
        identity.save()
        return identity
    kw.setdefault("organization", membership.organization)
    kw.setdefault("kind", ClientKindChoices.DEVELOPMENT.value)
    kw.setdefault("role", ClientRoleChoices.INTERFACE.value)
    kw.setdefault("client_id", generate_client_id())
    kw.setdefault("token_endpoint_auth_method", "none")
    kw.setdefault("grant_types", "urn:ietf:params:oauth:grant-type:device_code urn:fakts:grant-type:redeem refresh_token authorization_code")
    kw.setdefault("scope", "openid profile email")
    return fmodels.Client.objects.create(release=release, membership=membership, **kw)


def make_hub(organization: Organization | None = None, **kw) -> fmodels.Hub:
    if organization is None:
        organization = make_organization()
    n = _n()
    kw.setdefault("name", f"hub-{n}")
    kw.setdefault("identifier", f"comp{n}")
    kw.setdefault("creator", organization.owner)
    return fmodels.Hub.objects.create(organization=organization, **kw)


def make_service(organization: Organization | None = None, **kw) -> fmodels.Service:
    n = _n()
    kw.setdefault("name", f"Service {n}")
    kw.setdefault("identifier", f"com.example.service{n}")
    kw.setdefault("organization", organization or make_organization())
    return fmodels.Service.objects.create(**kw)


def make_service_release(service: fmodels.Service | None = None, **kw) -> fmodels.ServiceRelease:
    if service is None:
        service = make_service()
    kw.setdefault("version", "1.0.0")
    return fmodels.ServiceRelease.objects.create(service=service, **kw)


def make_service_instance(hub: fmodels.Hub | None = None, release: fmodels.ServiceRelease | None = None, **kw) -> fmodels.ServiceInstance:
    if hub is None:
        hub = make_hub()
    if release is None:
        release = make_service_release()
    kw.setdefault("organization", hub.organization)
    kw.setdefault("steward", hub.creator)
    kw.setdefault("template", "{}")
    kw.setdefault("token", f"instance-token-{_n()}")
    return fmodels.ServiceInstance.objects.create(hub=hub, release=release, **kw)


def make_device_code(**kw) -> fmodels.DeviceCode:
    kw.setdefault("code", f"device-code-{_n()}")
    kw.setdefault("secret", f"device-secret-{_n()}-{uuid4().hex}")
    kw.setdefault(
        "staging_manifest",
        {"identifier": "com.example.app", "version": "1.0.0", "scopes": [], "requirements": []},
    )
    kw.setdefault("expires_at", timezone.now() + timedelta(seconds=300))
    if "client" not in kw:
        # An unbound staged client, as /o/app-authorization/ would register.
        kw["client"] = fmodels.Client.objects.create(
            client_id=generate_client_id(),
            token_endpoint_auth_method="none",
            grant_types="urn:ietf:params:oauth:grant-type:device_code refresh_token",
        )
    return fmodels.DeviceCode.objects.create(**kw)


def make_redeem_token(hub: fmodels.Hub | None = None, **kw) -> fmodels.RedeemToken:
    if hub is None:
        hub = make_hub()
    kw.setdefault("user", hub.creator)
    kw.setdefault("token", str(uuid4()))
    return fmodels.RedeemToken.objects.create(hub=hub, **kw)
