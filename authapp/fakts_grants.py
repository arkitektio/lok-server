"""
authapp.fakts_grants

The canonical fakts grant: OAuth2 token-endpoint grants for dynamically
registered (fakts-provisioned) clients.

Two grants live here, plus a mixin shared with the standard grants:

- ``FaktsDeviceCodeGrant`` — the RFC 8628 device-code grant. ``/f/start/``
  dynamically registers a *public* ``OAuth2Client`` and stages a
  ``fakts.DeviceCode``; a human approves it in the kontrol frontend (choosing a
  hub, and thereby an organization); the device polls ``/o/token/`` with its
  ``device_code`` + ``client_id`` and receives the access token, a refresh
  token, and the rendered service instances in one response.

- ``FaktsRedeemGrant`` — the headless counterpart. A pre-issued
  ``fakts.RedeemToken`` (always bound to a hub, and through it an organization)
  plus a client manifest is exchanged directly for the same combined response;
  the fakts client is provisioned on first redeem.

- ``FaktsEnvelopeMixin`` — appends the fakts envelope (``self``, ``instances``,
  ``statuses``, ``client_id``) to any token response whose OAuth2 client is
  backed by a fakts client. Also mixed into the refresh-token and
  authorization-code grants, so instances are re-rendered on every refresh and
  website-kind clients receive them through the standard code flow. It also
  carries ``please_report`` — the operator's standing request for a client to
  re-report its configuration, which the client therefore picks up on its next
  refresh.

Org scoping: every token minted here has a ``karakter.Membership`` as its
subject (``OAuth2Token.user`` is a FK to Membership), so the issuing
organization is pinned at the database level and lands in the JWT as
``org`` (its primary key).

fakts imports are deliberately lazy — ``fakts.models`` imports
``authapp.models``, so importing fakts at module level here would cycle.
"""

import json
import logging

from django.conf import settings
from django.utils import timezone

from authlib.oauth2.rfc6749 import BaseGrant, TokenEndpointMixin
from authlib.oauth2 import OAuth2Error
from authlib.oauth2.rfc6749.errors import (
    InvalidGrantError,
    InvalidRequestError,
    InvalidScopeError,
)
from authlib.oauth2.rfc8628 import DeviceCodeGrant

logger = logging.getLogger(__name__)

FAKTS_REDEEM_GRANT_TYPE = "urn:fakts:grant-type:redeem"


class FaktsEnvelopeMixin:
    """Append the fakts envelope to a token response dict.

    Must be called *after* ``save_token`` — ``save_token`` splats the token dict
    into the ``OAuth2Token`` model constructor, so envelope keys added earlier
    would crash it.
    """

    def append_fakts_envelope(self, token: dict) -> dict:
        from django.core.exceptions import ObjectDoesNotExist

        from fakts.services.rendering import render_envelope, render_hub_envelope

        client = self.request.client

        # The operator's "please re-report your configuration" flag. Set *before*
        # any of the early returns below: a client whose envelope fails to render
        # is exactly the client an operator most wants to hear from, so the flag
        # must survive a rendering failure (and a request object we cannot see).
        # Only ever emitted as `True` — a client with nothing pending gets no key
        # at all, which keeps the token response unchanged for everyone else.
        if getattr(client, "report_requested_at", None) is not None:
            token["please_report"] = True

        # `_request` is the raw Django HttpRequest behind authlib's
        # DjangoOAuth2Request — needed because instance aliases render
        # host-aware (relative aliases resolve against the request host).
        http_request = getattr(self.request, "_request", None)
        if http_request is None:
            return token

        # The unified client tells us what it is: a hub identity renders the
        # hub config, an app client (has a release) its instances; relying
        # parties get no envelope.
        try:
            hub = client.hub_identity
        except (ObjectDoesNotExist, AttributeError):
            hub = None

        if hub is None and not client.release_id:
            return token

        try:
            if hub is not None:
                envelope = render_hub_envelope(http_request, hub)
            else:
                envelope = render_envelope(http_request, client)
        except Exception:
            # The token itself is valid either way; a rendering failure must not
            # turn a successful grant into a 500. The client can re-render on
            # its next refresh.
            logger.exception("Could not render fakts envelope for client %s", client.client_id)
            return token

        token.update(envelope)
        token["client_id"] = client.client_id
        return token


class FaktsDeviceCodeGrant(FaktsEnvelopeMixin, DeviceCodeGrant):
    """RFC 8628 device-code grant over ``fakts.DeviceCode``.

    The polled credential is the ``fakts.DeviceCode`` staged by ``/f/start/``
    (which also dynamically registered the public ``OAuth2Client`` this grant
    authenticates as, via the ``none`` method). Approval happens in the kontrol
    frontend through the ``acceptDeviceCode`` mutation, which binds the code to
    a fakts ``Client`` carrying the approving user's membership in the chosen
    hub's organization.
    """

    # Public clients only: fakts clients have no secret.
    TOKEN_ENDPOINT_AUTH_METHODS = ["none"]

    def query_device_credential(self, device_code):
        """The polled `device_code` is the staged authorization's full-entropy
        secret — one model serves the app and hub flows alike."""
        from fakts import models as fakts_models

        try:
            return fakts_models.DeviceCode.objects.get(secret=device_code)
        except fakts_models.DeviceCode.DoesNotExist:
            return None

    def query_user_grant(self, user_code):
        """`user_code` is the staged authorization's short human code (what the
        configure URL carries). The staged client always exists (it was
        registered at start); approval is it having been *bound* to a
        membership. Denied → access_denied; unbound → authorization_pending."""
        from fakts import models as fakts_models

        try:
            device_code = fakts_models.DeviceCode.objects.select_related("client__membership").get(code=user_code)
        except fakts_models.DeviceCode.DoesNotExist:
            return None

        if device_code.denied:
            return None, False
        if device_code.client.membership_id is not None:
            return device_code.client.membership, True
        return None

    def should_slow_down(self, credential):
        """RFC 8628 §3.5: polling faster than ``interval`` earns a slow_down."""
        now = timezone.now()
        last = credential.last_polled_at
        credential.last_polled_at = now
        credential.save(update_fields=["last_polled_at"])
        if last is None:
            return False
        return (now - last).total_seconds() < credential.interval

    def create_token_response(self):
        client = self.request.client
        credential = self.request.credential
        try:
            scope = credential.get_scope()
            token = self.generate_token(
                user=self.request.user,
                scope=scope,
                include_refresh_token=client.check_grant_type("refresh_token"),
            )
            self.save_token(token)
        except OAuth2Error:
            raise
        except Exception as e:
            # Anything unexpected on the credential → token path (a membership
            # that vanished between approval and poll, a claims lookup failing,
            # ...) must surface as an OAuth error, not a 500 at /o/token/.
            logger.exception("Device-code grant failed to mint tokens for client %s", client.client_id)
            raise InvalidGrantError(description=str(e))
        self.append_fakts_envelope(token)
        self.append_mesh_key(token, credential)
        # Single-use: burn the code once it has yielded its tokens. Continuity
        # from here on is the refresh-token chain, not the device code.
        credential.delete()
        return 200, token, self.TOKEN_RESPONSE_HEADER


    @staticmethod
    def append_mesh_key(token: dict, credential) -> dict:
        """Hand an app or hub its mesh key, minted at accept (``request_auth_key``).

        Only here, on the initial device-code response: the code is burned right
        after, so the key goes out exactly once and never rides along a refresh.
        Apps and hubs get the same ``mesh`` shape (``MeshClaim``); the identity
        it belongs to (sub, organization, hub) is in the envelope's ``self``.
        """
        from fakts.base_models import MeshClaim

        key = getattr(credential, "auth_key", None)
        if key is not None:
            token["mesh"] = MeshClaim(
                ionscale_auth_key=key.key,
                ionscale_coord_url=settings.IONSCALE_COORD_URL,
            ).model_dump()
        return token


class FaktsRedeemGrant(FaktsEnvelopeMixin, BaseGrant, TokenEndpointMixin):
    """Headless fakts grant: a pre-issued redeem token + manifest → tokens + instances.

    There is no pre-registered client to authenticate — the redeem token *is*
    the credential, and the fakts client (with its public OAuth2 client) is
    provisioned or reused during validation. The token's subject is the
    membership of the redeem token's user in its hub's organization.
    """

    GRANT_TYPE = FAKTS_REDEEM_GRANT_TYPE
    TOKEN_ENDPOINT_AUTH_METHODS = ["none"]

    def validate_token_request(self):
        from fakts import base_models, enums
        from fakts import models as fakts_models
        from fakts.services import clients as client_services

        data = self.request.payload.data

        redeem_token = data.get("redeem_token")
        if not redeem_token:
            raise InvalidRequestError("Missing 'redeem_token' in request.")

        raw_manifest = data.get("manifest")
        if not raw_manifest:
            raise InvalidRequestError("Missing 'manifest' in request.")

        try:
            manifest = base_models.Manifest(**json.loads(raw_manifest))
        except Exception as e:
            raise InvalidRequestError(f"Malformed 'manifest': {e}")

        role = data.get("requested_client_role", enums.ClientRoleVanilla.INTERFACE.value)
        valid_roles = [r.value for r in enums.ClientRoleVanilla]
        if role not in valid_roles:
            raise InvalidRequestError(
                f"Invalid 'requested_client_role' {role!r}; expected one of {', '.join(valid_roles)}."
            )

        try:
            fakts_client = client_services.redeem_token(redeem_token, manifest, role=role)
        except fakts_models.RedeemToken.DoesNotExist:
            raise InvalidGrantError(description="Invalid redeem token.")
        except client_services.RedeemTokenExhausted as e:
            raise InvalidGrantError(description=str(e))
        except client_services.RedeemTokenExpired:
            raise InvalidGrantError(description="Redeem token expired.")
        except client_services.RedeemTokenManifestChanged as e:
            raise InvalidGrantError(description=str(e))
        except client_services.RedeemTokenManifestMismatch as e:
            raise InvalidGrantError(description=str(e))
        except client_services.UnknownScope as e:
            raise InvalidScopeError(description=str(e))
        except client_services.DeviceAuthRequired as e:
            raise InvalidGrantError(description=str(e))
        except OAuth2Error:
            raise
        except Exception as e:
            # redeem_token/bind_client can fail in many domain-specific ways
            # (logo download, a hub whose owner lost membership, ...). Every one
            # of them is an OAuth error to the caller — never a 500.
            logger.exception("Redeem grant failed for manifest %s", manifest.identifier)
            raise InvalidGrantError(description=str(e))

        membership = fakts_client.membership
        if membership is None:
            raise InvalidGrantError(description="The redeemed client is not attached to an organization membership.")

        self.request.client = fakts_client
        self.request.user = membership

    def create_token_response(self):
        client = self.request.client
        token = self.generate_token(
            user=self.request.user,
            scope=client.scope,
            include_refresh_token=True,
        )
        self.save_token(token)
        self.append_fakts_envelope(token)
        return 200, token, self.TOKEN_RESPONSE_HEADER
