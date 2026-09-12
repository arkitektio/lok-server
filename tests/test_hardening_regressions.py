"""Regressions for the remaining audit findings.

Covers the user-directory scoping, device-code entropy/lifetime/single-use, the
`change_org` open redirect, and proof-of-possession on the device-code declines.
"""

import datetime

import pathlib

import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone

from fakts.services.device_codes import MAX_DEVICE_CODE_LIFETIME_SECONDS, _expires_at
from fakts.services.tokens import create_challenge_code, create_device_code
from karakter.views import safe_redirect_target
from tests import factories


class TestDeviceCodeEntropy:
    def test_alphabet_is_wider_than_hex(self):
        """The old implementation kept the last char of a uuid4 string — a hex
        digit — eight times: 16**8, not the 122 bits per uuid4 it looked like."""
        seen = {ch for _ in range(400) for ch in create_device_code()}
        assert len(seen) > 16, f"alphabet is only {len(seen)} symbols wide"

    def test_codes_are_long_enough_and_unique(self):
        codes = [create_device_code() for _ in range(2000)]
        assert len({len(c) for c in codes}) == 1
        assert len(codes[0]) >= 10
        # ~2**48 of space: 2000 draws should not collide.
        assert len(set(codes)) == len(codes)

    def test_codes_avoid_visually_ambiguous_symbols(self):
        """It is transcribed by a human off one screen into another device."""
        joined = "".join(create_device_code() for _ in range(200))
        for ambiguous in ("0", "O", "1", "I", "L", "U", "V"):
            assert ambiguous not in joined

    def test_challenge_code_is_a_full_entropy_secret(self):
        """Never shown to a human, so it is not constrained by transcription."""
        code = create_challenge_code()
        assert len(code) >= 40
        assert len({create_challenge_code() for _ in range(200)}) == 200


class TestDeviceCodeLifetime:
    def test_caller_supplied_lifetime_is_clamped(self):
        """`expiration_time_seconds` arrives in an unauthenticated request body."""
        deadline = _expires_at(60 * 60 * 24 * 365)
        assert deadline <= timezone.now() + datetime.timedelta(
            seconds=MAX_DEVICE_CODE_LIFETIME_SECONDS + 5
        )

    def test_reasonable_lifetime_is_preserved(self):
        deadline = _expires_at(300)
        assert deadline > timezone.now() + datetime.timedelta(seconds=290)

    def test_nonsense_lifetime_does_not_produce_a_past_deadline(self):
        assert _expires_at(0) > timezone.now()
        assert _expires_at(-1) > timezone.now()


class TestOpenRedirect:
    def test_relative_paths_are_allowed(self):
        assert safe_redirect_target("/organizations") == "/organizations"

    def test_absolute_foreign_urls_are_refused(self):
        assert safe_redirect_target("https://evil.tld/phish") != "https://evil.tld/phish"

    def test_protocol_relative_urls_are_refused(self):
        """`//evil.tld` has an empty scheme but a real netloc — the classic bypass
        for a check that only looks at the scheme."""
        assert safe_redirect_target("//evil.tld/phish") != "//evil.tld/phish"

    def test_empty_falls_back(self):
        assert safe_redirect_target(None)
        assert safe_redirect_target("")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_decline_requires_the_code_when_supplied():
    """A caller who presents a *wrong* code is refused even with a valid id."""
    from types import SimpleNamespace

    from api.management.mutations.device_code import (
        DeclineDeviceCodeInput,
        decline_device_code,
    )
    from fakts.models import DeviceCode

    device_code = await sync_to_async(factories.make_device_code)()

    def _decline(code):
        return decline_device_code(
            SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=None))),
            DeclineDeviceCodeInput(device_code=str(device_code.id), code=code),
        )

    with pytest.raises(Exception) as excinfo:
        await sync_to_async(_decline)("not-the-right-code")
    assert "not authorized" in str(excinfo.value).lower()

    fresh = await sync_to_async(DeviceCode.objects.get)(pk=device_code.pk)
    assert fresh.denied is False

    # The real code still works.
    await sync_to_async(_decline)(device_code.code)
    fresh = await sync_to_async(DeviceCode.objects.get)(pk=device_code.pk)
    assert fresh.denied is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_friends_only_returns_users_sharing_an_organization():
    """`friends` was `User.objects.all()` — every email in the deployment to
    anyone who registered an account."""
    from api.management.schema import schema
    from tests.conftest import build_auth_context

    def _setup():
        mine = factories.make_membership()
        stranger = factories.make_membership()
        colleague = factories.make_membership(organization=mine.organization)
        client = factories.make_client(membership=mine)
        return (
            build_auth_context(mine.user, mine.organization, client),
            mine,
            colleague,
            stranger,
        )

    context, mine, colleague, stranger = await sync_to_async(_setup)()

    result = await schema.execute("query { friends { id email } }", context_value=context)

    assert not result.errors, result.errors
    returned = {row["id"] for row in result.data["friends"]}
    assert str(mine.user.id) in returned
    assert str(colleague.user.id) in returned, "a colleague should still be visible"
    assert str(stranger.user.id) not in returned, "another tenant's user leaked"


class TestCommittedKeyMaterialGuard:
    """The repo's `config.yaml` is the default config source *and* is tracked in
    git, so a deployment that forgets to mount its own would sign id_tokens with
    a key anyone can read out of the repository."""

    @staticmethod
    def _repo_config():
        """The historically-committed config, read out of git history.

        `config.yaml` is no longer tracked, so this reads the last revision that
        still carried it. The key material in there is public forever — git
        history keeps it — which is exactly why the guard has to keep matching it.
        """
        import subprocess

        import yaml

        raw = subprocess.check_output(
            ["git", "log", "--format=%H", "-1", "--", "config.yaml"], text=True
        ).strip()
        if not raw:
            pytest.skip("config.yaml never existed in this history")

        # That newest commit touching the path is the one that *deleted* the
        # file, so the blob is not reachable at that revision — only at its
        # parent. (When the file still exists it is a plain modification and the
        # first read succeeds, so try both rather than assuming either.)
        for rev in (raw, f"{raw}^"):
            try:
                blob = subprocess.check_output(
                    ["git", "show", f"{rev}:config.yaml"], text=True, stderr=subprocess.DEVNULL
                )
            except subprocess.CalledProcessError:
                continue
            return yaml.safe_load(blob)

        pytest.skip("config.yaml is not readable from this history (shallow clone?)")

    def test_config_yaml_is_not_tracked(self):
        """It was listed in .gitignore but tracked anyway, so it shipped in the
        image as the *default* config source."""
        import subprocess

        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "config.yaml"],
            capture_output=True,
        )
        assert tracked.returncode != 0, "config.yaml is tracked in git again"

    def test_config_yaml_is_excluded_from_the_image(self):
        assert "config.yaml" in pathlib.Path(".dockerignore").read_text().split()

    def test_production_boot_refuses_the_committed_keys(self):
        from lok_server.configuration import Settings

        cfg = self._repo_config()
        cfg["django"]["debug"] = False

        with pytest.raises(Exception) as excinfo:
            Settings(**cfg)
        assert "committed" in str(excinfo.value).lower()

    def test_debug_does_not_bypass_the_guard(self):
        """`debug` used to short-circuit the guard — and the shipped config.yaml
        set `debug: true`, so the guard never fired on the one configuration it
        exists to catch. Opting out is now explicit.
        """
        from lok_server.configuration import Settings

        cfg = self._repo_config()
        cfg["django"]["debug"] = True

        with pytest.raises(Exception) as excinfo:
            Settings(**cfg)
        assert "committed" in str(excinfo.value).lower()

    def test_explicit_opt_out_allows_the_committed_keys(self, monkeypatch):
        """The escape hatch still exists, but you have to say so deliberately."""
        from lok_server.configuration import Settings

        monkeypatch.setenv("LOK_ALLOW_COMMITTED_KEYS", "1")
        cfg = self._repo_config()
        cfg["django"]["debug"] = False

        Settings(**cfg)

    def test_fresh_keys_pass_in_production(self):
        from lok_server.configuration import Settings

        cfg = self._repo_config()
        cfg["django"]["debug"] = False
        cfg["django"]["secret_key"] = "a-freshly-generated-secret-key-value"
        cfg["private_key"] = "-----BEGIN RSA PRIVATE KEY-----\nnot-the-committed-one\n-----END RSA PRIVATE KEY-----\n"

        Settings(**cfg)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_invite_by_code_still_resolves_for_an_anonymous_visitor():
    """`inviteByCode` is the only entry in `PUBLIC_ROOT_FIELDS` and powers the
    real `/invite/:code` page. `ManagementUser` just gained a `get_queryset` that
    dereferences `request.user`; if `createdBy` resolution ran it, the public
    invite page would start 500ing for logged-out visitors.

    This also settles the open question from the audit: whether nested resolvers
    under the one public root field expose user PII to anonymous callers.
    """
    from django.contrib.auth.models import AnonymousUser
    from kante.context import HttpContext, TemporalResponse, UniversalRequest

    from api.management.schema import schema
    from karakter.models import Invite

    def _public_invite():
        membership = factories.make_membership()
        org = membership.organization
        return Invite.objects.create(created_by=org.owner, created_for=org, public=True)

    invite = await sync_to_async(_public_invite)()

    request = UniversalRequest(_extensions={})
    request.set_user(AnonymousUser())
    context = HttpContext(
        request=request, response=TemporalResponse(), headers={}, type="http"
    )

    result = await schema.execute(
        "query ($c: String!) { inviteByCode(inviteCode: $c) { id } }",
        context_value=context,
        variable_values={"c": str(invite.token)},
    )

    assert not result.errors, f"public invite preview broke: {result.errors}"
    assert result.data["inviteByCode"]["id"] == str(invite.id)


@pytest.mark.django_db(transaction=True)
def test_a_granted_device_code_is_single_use():
    """Burning the code on grant is visible to devices: a second poll of the
    token endpoint no longer returns tokens. Pin what it does return."""
    from django.test import Client as DjangoClient

    from fakts.models import DeviceCode

    membership = factories.make_membership()
    fakts_client = factories.make_client(membership=membership)
    device_code = factories.make_device_code(
        client=fakts_client,
        granted_scope="openid",
    )
    fakts_client.token_endpoint_auth_method = "none"
    fakts_client.grant_types = "urn:ietf:params:oauth:grant-type:device_code refresh_token"
    fakts_client.scope = "openid"
    fakts_client.save()

    http = DjangoClient()

    data = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": device_code.secret,
        "client_id": fakts_client.client_id,
    }
    first = http.post("/lok/o/token/", data=data, secure=True)
    assert first.status_code == 200
    assert first.json()["access_token"]

    # The row is gone, so the same code cannot be redeemed again.
    assert not DeviceCode.objects.filter(pk=device_code.pk).exists()

    second = http.post("/lok/o/token/", data=data, secure=True)
    assert second.status_code == 400


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_anonymous_invite_preview_does_not_leak_the_inviter_email():
    """`inviteByCode` is public and `createdBy` is an FK traversal, which does not
    run `ManagementUser.get_queryset` — so the email needed its own gate. The
    kontrol invite page renders `username` and the avatar, never the email."""
    from django.contrib.auth.models import AnonymousUser
    from kante.context import HttpContext, TemporalResponse, UniversalRequest

    from api.management.schema import schema
    from karakter.models import Invite

    def _mk():
        membership = factories.make_membership()
        org = membership.organization
        org.owner.email = "inviter@secret.example"
        org.owner.save()
        return Invite.objects.create(created_by=org.owner, created_for=org, public=True)

    invite = await sync_to_async(_mk)()

    request = UniversalRequest(_extensions={})
    request.set_user(AnonymousUser())
    context = HttpContext(
        request=request, response=TemporalResponse(), headers={}, type="http"
    )

    result = await schema.execute(
        "query ($c: String!) { inviteByCode(inviteCode: $c) { id createdBy { username email } } }",
        context_value=context,
        variable_values={"c": str(invite.token)},
    )

    assert not result.errors, result.errors
    created_by = result.data["inviteByCode"]["createdBy"]
    # The page still works and still identifies the inviter...
    assert created_by["username"]
    # ...but the address is withheld.
    assert created_by["email"] is None
