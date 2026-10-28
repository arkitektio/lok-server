"""
authapp.fakts_grants

The canonical fakts grant: OAuth2 token-endpoint grants for dynamically
registered (fakts-provisioned) clients.

Two grants live here, plus a mixin shared with the standard grants:

- ``FaktsDeviceCodeGrant`` — the RFC 8628 device-code grant.
  ``/o/app-authorization/`` dynamically registers a *public* ``Client`` and stages
  a ``fakts.DeviceCode``; a human approves it in the kontrol frontend (choosing a
  hub, and thereby an organization); the device polls ``/o/token/`` with its
  ``device_code`` + ``client_id`` and receives the access token, a refresh
  token, and the rendered service instances in one response.

- ``FaktsMandateKeyGrant`` — the headless counterpart. A mandate key (see
  ``fakts.models.Mandate``) plus a client manifest is exchanged for an access
  token and the same envelope — but never a refresh token: the client runs the
  grant again when its access token expires, so every token it holds is backed
  by a fresh check of the mandate and its issuers.

- ``FaktsEnvelopeMixin`` — appends the fakts envelope (``self``, ``instances``,
  ``statuses``, ``client_id``) to any token response whose OAuth2 client is
  backed by a fakts client. Also mixed into the refresh-token and
  authorization-code grants, so instances are re-rendered on every refresh and
  website-kind clients receive them through the standard code flow. It also
  carries ``please_report`` — the operator's standing request for a client to
  re-report its configuration, which the client therefore picks up on its next
  refresh — and hands out a mesh key once (``append_mesh_key``).

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
)
from authlib.oauth2.rfc8628 import DeviceCodeGrant

logger = logging.getLogger(__name__)

FAKTS_MANDATE_KEY_GRANT_TYPE = "urn:fakts:grant-type:mandate-key"


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
        # DjangoOAuth2Request — needed because the envelope renders
        # host-aware (its `self` alias is the request host).
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


    @staticmethod
    def append_mesh_key(token: dict, key) -> dict:
        """Hand an app or hub its mesh key (``request_auth_key``), exactly once.

        The device-code grant hands out the key minted at accept (the code is
        burned right after); the mandate-key grant one minted for this grant. A
        refresh never carries one. Apps and hubs get the same ``mesh`` shape
        (``MeshClaim``); the identity it belongs to (sub, organization, hub) is in
        the envelope's ``self``.
        """
        from fakts.base_models import MeshClaim

        if key is not None:
            token["mesh"] = MeshClaim(
                ionscale_auth_key=key.key,
                ionscale_coord_url=settings.IONSCALE_COORD_URL,
            ).model_dump()
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
        self.append_mesh_key(token, credential.auth_key)
        # Single-use: burn the code once it has yielded its tokens. Continuity
        # from here on is the refresh-token chain, not the device code.
        credential.delete()
        return 200, token, self.TOKEN_RESPONSE_HEADER



class FaktsMandateKeyGrant(FaktsEnvelopeMixin, BaseGrant, TokenEndpointMixin):
    """Headless fakts grant: a mandate key + manifest → an access token + instances.

    There is no pre-registered client to authenticate: the key is the credential,
    and the client it logs in as (one per mandate and device) is created or reused
    while validating. The token's subject is the mandate's grantor membership. No
    refresh token is issued; see the module docstring.
    """

    GRANT_TYPE = FAKTS_MANDATE_KEY_GRANT_TYPE
    TOKEN_ENDPOINT_AUTH_METHODS = ["none"]

    def validate_token_request(self):
        from pydantic import ValidationError

        from fakts.base_models import MandateKeyGrantRequest, MandatePinMismatch
        from fakts.services import clients as client_services
        from fakts.services import mandates

        data = self.request.payload.data
        raw_manifest = data.get("manifest")
        if not data.get("key"):
            raise InvalidRequestError("Missing 'key' in request.")
        if not raw_manifest:
            raise InvalidRequestError("Missing 'manifest' in request.")
        try:
            request = MandateKeyGrantRequest(
                key=data.get("key"),
                manifest=json.loads(raw_manifest),
                **{
                    field: data[field]
                    for field in ("requested_client_kind", "requested_client_role", "request_auth_key")
                    if data.get(field) is not None
                },
            )
        except ValidationError as e:
            # One line: an OAuth error description may not carry newlines.
            problems = "; ".join(f"{'.'.join(str(p) for p in err['loc']) or 'request'}: {err['msg']}" for err in e.errors())
            raise InvalidRequestError(f"Malformed request: {problems}")
        except ValueError as e:
            raise InvalidRequestError(f"Malformed 'manifest': {e}")

        try:
            grant = mandates.authenticate_key(request)
        except (mandates.MandateError, MandatePinMismatch, client_services.DeviceAuthRequired) as e:
            raise InvalidGrantError(description=str(e))
        except OAuth2Error:
            raise
        except Exception as e:
            # Binding can fail in many domain-specific ways (a logo download, ...).
            # Every one of them is an OAuth error to the caller, never a 500.
            logger.exception("Mandate-key grant failed for manifest %s", request.manifest.identifier)
            raise InvalidGrantError(description=str(e))

        self.request.client = grant.client
        self.request.user = grant.client.membership
        self.mesh_key = grant.mesh_key

    def create_token_response(self):
        from authapp.models import OAuth2Token
        from authapp.server import MAX_ACCESS_TOKEN_EXPIRES_IN

        client = self.request.client
        token = self.generate_token(
            user=self.request.user,
            scope=client.scope,
            include_refresh_token=False,
        )
        self.save_token(token)
        # Rows of access tokens that have expired are of no further use (there is
        # no chain to detect reuse on); one grant an hour would pile them up.
        OAuth2Token.objects.filter(
            client_id=client.client_id,
            refresh_token__isnull=True,
            issued_at__lt=int(timezone.now().timestamp()) - MAX_ACCESS_TOKEN_EXPIRES_IN,
        ).delete()
        self.append_fakts_envelope(token)
        self.append_mesh_key(token, self.mesh_key)
        return 200, token, self.TOKEN_RESPONSE_HEADER
