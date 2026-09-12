import json
import logging
import secrets

from django.conf import settings
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import View

from authapp.bearer import InvalidBearerToken, decode_bearer_token
from authapp.views import issuer_absolute_uri, issuer_base_url
from authapp.throttle import AUTHORIZATION_LIMIT_PER_MINUTE, is_throttled, throttled_response
from fakts import base_models, models
from fakts.services import clients, device_codes, rendering

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Error contract
#
# Every fakts REST error is an HTTP 4xx/5xx with a structured JSON body:
#
#     {"status": "error", "error": "<code>", "error_description": "<text>",
#      "details": <validation details when available>}
#
# ``error`` uses the OAuth / RFC 8628 vocabulary (invalid_request, invalid_grant,
# access_denied, expired_token, authorization_pending, slow_down, server_error)
# so fakts clients can branch on the same codes they already handle at the
# token endpoint. ``status: "error"`` is kept for back-compat with clients that
# only ever looked at ``status``.
# --------------------------------------------------------------------------- #

ERROR_INVALID_REQUEST = "invalid_request"
ERROR_INVALID_GRANT = "invalid_grant"
ERROR_ACCESS_DENIED = "access_denied"
ERROR_EXPIRED_TOKEN = "expired_token"
ERROR_AUTHORIZATION_PENDING = "authorization_pending"
ERROR_SLOW_DOWN = "slow_down"
ERROR_SERVER_ERROR = "server_error"


def _error(
    code: str,
    description: str,
    *,
    http_status: int = 400,
    details=None,
    legacy_status: str = "error",
    **extra,
) -> JsonResponse:
    """Build the structured fakts error envelope (see the module comment above).

    ``legacy_status`` is the value of the back-compat ``status`` field (the mesh
    poll endpoint historically answered ``pending``/``denied``/``expired``
    there); ``extra`` keys (e.g. a legacy ``message``) are merged into the body.
    """
    body = {"status": legacy_status, "error": code, "error_description": description}
    if details is not None:
        body["details"] = details
    body.update(extra)
    return JsonResponse(body, status=http_status)


def _parse(request, model, *, error_key="error"):
    """Parse the JSON request body into ``model``.

    Returns ``(instance, None)`` on success or ``(None, JsonResponse)`` — an
    HTTP 400 ``invalid_request`` envelope (with pydantic's ``errors()`` under
    ``details`` when the body parsed but failed validation) on failure.

    ``error_key`` is a legacy knob: older clients read the human text from
    ``message`` on some endpoints, so the description is mirrored there when
    asked.
    """
    try:
        payload = json.loads(request.body)
    except Exception as e:
        logger.info("Malformed JSON body: %s", e)
        return None, _error(
            ERROR_INVALID_REQUEST,
            f"Malformed request: {e}",
            **({error_key: f"Malformed request: {e}"} if error_key != "error" else {}),
        )

    try:
        return model(**payload), None
    except Exception as e:
        logger.info("Request body failed validation for %s: %s", getattr(model, "__name__", model), e)
        details = e.errors() if hasattr(e, "errors") else None
        if details is not None:
            # pydantic's ErrorDetails carry the original input and a ctx that may
            # hold non-JSON-serialisable exception objects; keep only the safe keys.
            details = [{k: v for k, v in d.items() if k in ("type", "loc", "msg")} for d in details]
        return None, _error(
            ERROR_INVALID_REQUEST,
            f"Malformed request: {e}",
            details=details,
            **({error_key: f"Malformed request: {e}"} if error_key != "error" else {}),
        )


def _status(status, message):
    return JsonResponse({"status": status, "message": message})


def _absolute_configure_url(template: str, base_url: str) -> str:
    """Resolve the configured `configure_url` to an **absolute** URL for the client.

    The well-known must always hand the client an absolute URL, so we resolve the
    three shapes a deployment may configure:

    - a value with a scheme (``https://host/configure/{code}``) is used verbatim;
    - a root-relative path (``/configure/{code}``) is joined to ``base_url`` (the
      deployment's base domain);
    - a bare host (``go.arkitekt.live/configure/{code}``) is promoted to ``https``.

    The literal ``{code}`` placeholder is preserved — the client substitutes it with
    the device code, so we only ever concatenate, never ``.format()`` it here.
    """
    if "://" in template:
        return template
    if template.startswith("/"):
        return base_url.rstrip("/") + template
    return "https://" + template


@method_decorator(csrf_exempt, name="dispatch")
class WellKnownFakts(View):
    """Well Known fakts Viewset (only allows get). Sends back the well known
    configuration for the fakts server, describing the endpoints for "Claim",
    "Configure", the device-code "start" and "challenge" flows, as well as the
    name and version of the Fakts Protocol."""

    def get(self, request, format=None):
        from authapp.server import GRANT_TYPES_SUPPORTED, TOKEN_ENDPOINT_AUTH_METHODS_SUPPORTED

        # The deployment's base domain — also the base a root-relative configure_url
        # resolves against. (frontend_url is kept for back-compat but is deprecated in
        # favour of the explicit, always-absolute `configure` endpoint below.)
        base_domain = issuer_base_url(request)
        return JsonResponse(
            data=base_models.WellKnownFakts(
                name=settings.DEPLOYMENT_NAME,
                version=settings.FAKTS_PROTOCOL_VERSION,
                description=settings.DEPLOYMENT_DESCRIPTION,
                base_url=issuer_absolute_uri(request, "fakts:index"),
                frontend_url=base_domain,
                configure=_absolute_configure_url(settings.DEPLOYMENT_CONFIGURE_URL, base_domain),
                issuer=settings.OIDC_ISSUER,
                device_authorization_endpoint=issuer_absolute_uri(request, "app_authorization"),
                token_endpoint=issuer_absolute_uri(request, "token"),
                jwks_uri=issuer_absolute_uri(request, "jwks"),
                grant_types_supported=GRANT_TYPES_SUPPORTED,
                token_endpoint_auth_methods_supported=TOKEN_ENDPOINT_AUTH_METHODS_SUPPORTED,
                mesh_coord_url=settings.IONSCALE_COORD_URL,
                mesh_device_code_start=issuer_absolute_uri(request, "fakts:meshstart"),
                mesh_challenge_url=issuer_absolute_uri(request, "fakts:meshchallenge"),
                mesh_configure=_absolute_configure_url(settings.DEPLOYMENT_MESH_CONFIGURE_URL, base_domain),
                hub_authorization_endpoint=issuer_absolute_uri(request, "hub_authorization"),
                hub_claim=issuer_absolute_uri(request, "fakts:hubclaim"),
                hub_configure=_absolute_configure_url(settings.DEPLOYMENT_HUB_CONFIGURE_URL, base_domain),
            ).model_dump()
        )


@method_decorator(csrf_exempt, name="dispatch")
class AppAuthorizationView(View):
    """The app authorization endpoint — the canonical grant's front door,
    served at /o/app-authorization/ next to the token endpoint.

    Device authorization doubling as dynamic client registration: the manifest
    in the request mints a public OAuth2 client, and the app then polls the
    OAuth2 token endpoint with
    grant_type=urn:ietf:params:oauth:grant-type:device_code as that client.

    This is a **private** endpoint, not RFC 8628 §3.1: it takes a JSON body
    carrying a manifest rather than form-encoded `client_id`, and its response
    adds `status`/`client_id` and a `verification_uri` holding a literal
    `{code}` placeholder. It is therefore advertised only in
    /.well-known/fakts, never as `device_authorization_endpoint` in the OIDC
    or RFC 8414 metadata — a generic device-flow library reading those would
    otherwise fail here on its first request. The *grant* it feeds
    (authapp.fakts_grants.FaktsDeviceCodeGrant, at /o/token/) is conforming,
    which is why the grant type stays in `grant_types_supported`."""

    def post(self, request, *args, **kwargs):
        if is_throttled(request, "authorization", AUTHORIZATION_LIMIT_PER_MINUTE):
            return throttled_response()

        start_grant, err = _parse(request, base_models.DeviceCodeStartRequest)
        if err:
            return err

        try:
            device_code = device_codes.start_device_code(start_grant)
        except device_codes.LogoDownloadError as e:
            return _error(ERROR_INVALID_REQUEST, "Error downloading logo", details=[{"msg": str(e)}])

        # Opportunistically reap expired, never-approved codes so their orphan
        # dynamically-registered OAuth2 clients don't accumulate.
        device_codes.purge_expired_device_codes()

        base_domain = issuer_base_url(request)
        configure_template = _absolute_configure_url(settings.DEPLOYMENT_CONFIGURE_URL, base_domain)

        return JsonResponse(
            {
                "status": "granted",
                # `device_code` is the full-entropy polling secret; `user_code`
                # is the short human-transcribable code the configure URL carries.
                "device_code": device_code.secret,
                "user_code": device_code.code,
                "client_id": device_code.client.client_id,
                "token_endpoint": issuer_absolute_uri(request, "token"),
                "verification_uri": configure_template,
                "verification_uri_complete": configure_template.replace("{code}", device_code.code),
                "expires_in": device_code.get_expires_in(),
                "interval": device_code.interval,
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class HubAuthorizationView(View):
    """The hub authorization endpoint — the canonical grant's front door for
    whole-hub provisioning, served at /o/hub-authorization/.

    Same shape as app authorization: the hub manifest dynamically registers a
    public OAuth2 client alongside the staged code; a human accepts in kontrol
    (creating the hub inside an organization); the hub server then polls the
    token endpoint with the device-code grant and receives tokens + its
    rendered hub config in one response."""

    def post(self, request, *args, **kwargs):
        if is_throttled(request, "authorization", AUTHORIZATION_LIMIT_PER_MINUTE):
            return throttled_response()

        start_grant, err = _parse(request, base_models.HubStartRequest)
        if err:
            return err

        try:
            device_code = device_codes.start_hub_device_code(start_grant)
        except device_codes.LogoDownloadError as e:
            return _error(ERROR_INVALID_REQUEST, "Error downloading logo", details=[{"msg": str(e)}])

        device_codes.purge_expired_device_codes()

        base_domain = issuer_base_url(request)
        configure_template = _absolute_configure_url(settings.DEPLOYMENT_HUB_CONFIGURE_URL, base_domain)

        return JsonResponse(
            {
                "status": "granted",
                "device_code": device_code.secret,
                "user_code": device_code.code,
                "client_id": device_code.client.client_id,
                "token_endpoint": issuer_absolute_uri(request, "token"),
                "verification_uri": configure_template,
                "verification_uri_complete": configure_template.replace("{code}", device_code.code),
                "expires_in": device_code.get_expires_in(),
                "interval": device_code.interval,
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class MeshStartChallengeView(View):
    """Start endpoint for the mesh device-code flow (a machine joining an org mesh)."""

    def post(self, request, *args, **kwargs):
        # Same anonymous brute-force surface as app authorization; same budget.
        if is_throttled(request, "authorization", AUTHORIZATION_LIMIT_PER_MINUTE):
            return throttled_response()

        start_grant, err = _parse(request, base_models.MeshDeviceCodeStartRequest)
        if err:
            return err

        try:
            device_code = device_codes.start_mesh_device_code(start_grant)
        except Exception as e:
            logger.warning("Could not stage mesh device code: %s", e, exc_info=True)
            return _error(ERROR_INVALID_REQUEST, f"Could not start the mesh device-code flow: {e}")

        return JsonResponse({"status": "granted", "code": device_code.code, "challenge": device_code.challenge_code})


@method_decorator(csrf_exempt, name="dispatch")
class MeshChallengeView(View):
    """Poll endpoint for the mesh device-code flow.

    The mesh flow stays outside the OAuth token endpoint: the minted result is
    an ``IonscaleAuthKey`` (a tailnet pre-auth key, not an OAuth token) and the
    machine needs the coordination URL and the authorized machine name.
    """

    def post(self, request, *args, **kwargs):
        # Polling a secret challenge code is the brute-force target of this flow.
        if is_throttled(request, "authorization", AUTHORIZATION_LIMIT_PER_MINUTE):
            return throttled_response()

        challenge, err = _parse(request, base_models.DeviceCodeChallengeRequest)
        if err:
            return err

        try:
            device_code = models.MeshDeviceCode.objects.select_related("auth_key").get(challenge_code=challenge.code)
        except models.MeshDeviceCode.DoesNotExist:
            # Unknown *or already burned* — a granted code is single-use.
            return _error(ERROR_INVALID_GRANT, "Challenge does not exist")

        if timezone.now() > device_code.expires_at:
            device_code.delete()
            # RFC 8628 vocabulary; the legacy ``status`` keeps its old value.
            return _error(
                ERROR_EXPIRED_TOKEN,
                "The user has not given an answer in enough time",
                legacy_status="expired",
                message="The user has not given an answer in enough time",
            )

        if device_code.denied:
            device_code.delete()
            return _error(
                ERROR_ACCESS_DENIED,
                "The user has denied the request",
                legacy_status="denied",
                message="The user has denied the request",
            )

        auth_key = device_code.auth_key
        if auth_key:
            response = JsonResponse(
                {
                    "status": "granted",
                    "ionscale_auth_key": auth_key.key,
                    "ionscale_coord_url": settings.IONSCALE_COORD_URL,
                    "machine_name": device_code.machine_name,
                }
            )
            # Single-use: burn the code once it has yielded its key (mirrors the
            # device-code grant at the token endpoint). The minted auth key is
            # not touched — ``MeshDeviceCode.auth_key`` is SET_NULL the other way.
            device_code.delete()
            return response

        return _error(
            ERROR_AUTHORIZATION_PENDING,
            "User has not verified the challenge",
            legacy_status="pending",
            message="User has not verified the challenge",
        )


@method_decorator(csrf_exempt, name="dispatch")
class ClaimHubView(View):
    """Retrieve a hub faktsclaim given a hub token."""

    def post(self, request, *args, **kwargs):
        # The hub token is a long-lived bearer secret; make guessing it expensive.
        if is_throttled(request, "claim", AUTHORIZATION_LIMIT_PER_MINUTE):
            return throttled_response()

        claim, err = _parse(request, base_models.ServerClaimRequest, error_key="message")
        if err:
            return err

        # ``Hub.token`` is unique, so the indexed lookup finds at most one row;
        # the constant-time compare on top just avoids leaking anything through
        # the equality check itself.
        hub = models.Hub.objects.filter(token=claim.token).select_related("organization").first()
        if hub is None or not secrets.compare_digest(str(hub.token), str(claim.token)):
            return _error(ERROR_INVALID_GRANT, "No Hub found for this token", message="No Hub found for this token")

        try:
            context = rendering.create_serverlinking_context(request, hub, claim)
            config = rendering.render_server_fakts(hub, context)
            return JsonResponse({"status": "granted", "config": config.model_dump()})
        except Exception as e:
            logger.error(e, exc_info=True)
            return _error(
                ERROR_SERVER_ERROR,
                "Error creating configuration",
                http_status=500,
                message="Error creating configuration",
            )


@method_decorator(csrf_exempt, name="dispatch")
class ReportView(View):
    """Record a client's self-report (functional flag + alias reports).

    Authenticated with the client's Bearer access token — the JWT's `client_id`
    claim identifies the reporting client."""

    def post(self, request, *args, **kwargs):
        try:
            claims = decode_bearer_token(request)
        except InvalidBearerToken as e:
            return _error(ERROR_INVALID_GRANT, str(e), http_status=401, message=str(e))

        claim, err = _parse(request, base_models.ReportRequest, error_key="message")
        if err:
            return err

        try:
            client = models.Client.objects.get(client_id=claims.get("client_id"))
            clients.report_client(client, claim)
            return _status("reported", "Report processed successfully")
        except models.Client.DoesNotExist:
            return _error(
                ERROR_INVALID_GRANT,
                "No client found for this token",
                http_status=401,
                message="No client found for this token",
            )
        except Exception as e:
            logger.error(e, exc_info=True)
            return _error(
                ERROR_SERVER_ERROR,
                "Error processing report",
                http_status=500,
                message="Error processing report",
            )
