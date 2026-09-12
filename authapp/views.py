# authapp/views.py
"""
authapp.views

Lightweight Django views used by the authentication subsystem.

Provided views:
- issue_token(request): POST-only token endpoint that delegates to the
    project's AuthorizationServer instance (see `authapp.server`).
- CustomLoginView: small LoginView subclass wired to the project's
    login template and redirect behavior.
- logout_view: convenience wrapper around django.contrib.auth.logout.
- home_view: a login-protected home page that exposes the current user
    in the template context.

These docstrings aim to make the code easier to navigate for new
contributors and to clarify security-related decorators (CSRF, allowed
methods).
"""

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render, redirect
from django.contrib.auth import logout
from django.contrib.auth.views import LoginView
from django.urls import reverse_lazy, reverse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from authapp.server import server, resource_protector
from authlib.oauth2 import OAuth2Error
from django.views.decorators.csrf import csrf_exempt
from urllib.parse import urljoin, urlsplit, urlunsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from authapp.token_generators import public_jwks


@csrf_exempt
def jwks(request: HttpRequest) -> JsonResponse:
    """EXPOSE JWKS — the public half of the signing key only (see
    ``authapp.token_generators.public_jwks``)."""
    return JsonResponse(public_jwks())


@csrf_exempt
@resource_protector("openid")
def user_info(request: HttpRequest) -> JsonResponse:
    """The OIDC UserInfo endpoint (OIDC Core §5.3).

    Gated on `openid`, not `profile`: §5.3.1 says any access token obtained
    with the `openid` scope MUST be served here. Requiring `profile` happened
    to work only because `ensureopenid` provisions every relying party with
    "openid profile email"; a third-party RP asking for `openid` alone got a
    403. The individual claims are gated by scope instead (§5.4), which is
    where that gating belongs.
    """
    from fakts.models import Client
    from authapp.oidc_claims import build_claims

    membership = request.oauth_token.user  # type: ignore

    # Recover the per-client `email` policy from the token's client. The `sub`
    # here MUST match the id_token's `sub` for the same client (OIDC Core
    # §5.3.2), so the whole claim set is built by the same helper
    # grants.OpenIDCode uses.
    try:
        client = Client.objects.get(client_id=request.oauth_token.client_id)  # type: ignore
        email_template = client.email_template
    except Client.DoesNotExist:
        email_template = None

    return JsonResponse(
        build_claims(membership, request.oauth_token.scope, email_template)  # type: ignore
    )


def issuer_absolute_uri(request: HttpRequest, view_name: str) -> str:
    """Absolute URL for ``view_name``, anchored to the configured issuer.

    These used to be built with ``request.build_absolute_uri``, which derives the
    host from the request. With ``ALLOWED_HOSTS = ["*"]`` and
    ``USE_X_FORWARDED_HOST = True`` (both defaults) that host is the client's own
    ``X-Forwarded-Host`` header, so anyone could make the discovery document
    advertise their endpoints: a relying party bootstrapping from it would post
    its ``code`` and ``client_secret`` to the attacker's token endpoint and fetch
    ``jwks_uri`` from attacker infrastructure — letting the attacker also choose
    the key the RP validates id_tokens against.

    ``issuer`` was already pinned to config; every other endpoint in the document
    now shares that anchor. Falls back to the request only when ``oidc_issuer``
    is left unconfigured, which keeps a bare development server working.
    """
    path = reverse(view_name)
    issuer = (settings.OIDC_ISSUER or "").rstrip("/")
    if not issuer:
        return request.build_absolute_uri(path)
    return urljoin(issuer + "/", path.lstrip("/"))


def issuer_base_url(request: HttpRequest) -> str:
    """The deployment's base domain, anchored to the configured issuer.

    Same reasoning as :func:`issuer_absolute_uri`. This one matters for the
    human-facing half: a root-relative ``configure_url`` is resolved against
    this, and that is the URL a person is sent to in order to approve a device
    code — so letting the caller choose its host is a ready-made phishing
    primitive on the deployment's own name.
    """
    issuer = (settings.OIDC_ISSUER or "").rstrip("/")
    if not issuer:
        issuer = request.build_absolute_uri("/").rstrip("/")

    # Strip the script name from the *path*, never by string suffix: an issuer
    # of "http://lok" with script name "lok" ends with "/lok" in the host part,
    # and a naive endswith() would eat the host.
    parsed = urlsplit(issuer)
    script_name = (settings.MY_SCRIPT_NAME or "").strip("/")
    path = parsed.path.rstrip("/")
    if script_name and path.endswith(f"/{script_name}"):
        path = path[: -(len(script_name) + 1)]
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def _authorization_server_metadata(request: HttpRequest) -> dict:
    """The RFC 8414 authorization-server metadata core, shared by
    /.well-known/oauth-authorization-server and /.well-known/openid-configuration
    (which adds its OIDC-specific fields on top). /.well-known/fakts inlines the
    same core so fakts clients need a single discovery request."""
    from authapp.server import GRANT_TYPES_SUPPORTED, TOKEN_ENDPOINT_AUTH_METHODS_SUPPORTED

    return {
        "issuer": settings.OIDC_ISSUER,
        "authorization_endpoint": issuer_absolute_uri(request, "authorize"),
        "token_endpoint": issuer_absolute_uri(request, "token"),
        "jwks_uri": issuer_absolute_uri(request, "jwks"),
        "response_types_supported": ["code"],
        "scopes_supported": ["openid", "profile", "email"],
        "token_endpoint_auth_methods_supported": TOKEN_ENDPOINT_AUTH_METHODS_SUPPORTED,
        "grant_types_supported": GRANT_TYPES_SUPPORTED,
        "code_challenge_methods_supported": ["S256"],
        "revocation_endpoint": issuer_absolute_uri(request, "revoke"),
        "revocation_endpoint_auth_methods_supported": TOKEN_ENDPOINT_AUTH_METHODS_SUPPORTED,
        "response_modes_supported": ["query"],
        # There is deliberately NO `device_authorization_endpoint` here, even
        # though `urn:ietf:params:oauth:grant-type:device_code` is advertised
        # above. The *grant* at /o/token/ is conforming RFC 8628; the front
        # door that mints device codes (/o/app-authorization/) is not — it
        # takes a JSON manifest and doubles as dynamic client registration
        # rather than accepting the form-encoded `client_id` RFC 8628 §3.1
        # requires. Advertising it under the standard name told generic
        # device-flow libraries to speak a protocol lok does not answer, so
        # they failed on their first request with an opaque validation error.
        # fakts clients find it under the same key in /.well-known/fakts,
        # which is explicitly a private protocol document.
    }


@csrf_exempt
def oauth_authorization_server(request: HttpRequest) -> JsonResponse:
    """OAuth 2.0 Authorization Server Metadata (RFC 8414)."""
    return JsonResponse(_authorization_server_metadata(request))


@csrf_exempt
def open_id_configuration(request: HttpRequest) -> JsonResponse:
    """OpenID Configuration: the RFC 8414 core plus the OIDC-specific fields."""
    metadata = _authorization_server_metadata(request)
    metadata.update(
        {
            "userinfo_endpoint": issuer_absolute_uri(request, "user_info"),
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            # Every claim `authapp.oidc_claims.build_claims` can emit, in the
            # order of the scope that buys it. `org` and `roles` are lok's own
            # additions (§5.4 allows them); they are listed so relying parties
            # can discover them rather than having to be told out of band.
            # `roles` is UserInfo-only — build_claims omits it from the
            # id_token, where the list would be unbounded and signed.
            "claims_supported": [
                "sub",
                "iss",
                "aud",
                "exp",
                "iat",
                "auth_time",
                "nonce",
                "org",
                "roles",
                "name",
                "nickname",
                "preferred_username",
                "email",
            ],
        }
    )
    return JsonResponse(metadata)


@require_http_methods(["GET", "POST"])
def authorize(request: HttpRequest) -> HttpResponse:
    """The OAuth2/OIDC authorization endpoint.

    ``GET`` (the entry point relying parties redirect the browser to): when a
    session exists, validate the request via authlib and forward to the kontrol
    consent page (which renders the client, scopes and an organization picker);
    without a session, forward too — the SPA gates on login and returns here.

    ``POST`` (the consent decision, session-authenticated and CSRF-protected —
    the kontrol page submits a real form so the browser follows the resulting
    302 to the relying party): carries the original authorize parameters plus
    ``organization`` (slug) and ``allow``. The granted subject is the user's
    *membership* in that organization, so every code — like every token — is
    org-scoped at the database level.
    """
    from karakter.models import Membership

    if request.method == "GET":
        if request.user.is_authenticated:
            # Fail fast on an invalid request (unknown client, bad redirect_uri,
            # missing PKCE for a public client) before bouncing the user to the
            # consent UI.
            try:
                server.get_consent_grant(request=request, end_user=None)
            except OAuth2Error as error:
                return server.handle_response(*error())
        query = request.GET.urlencode()
        return redirect(f"{settings.KONTROL_FRONTEND_URL}/authorize?{query}")

    if not request.user.is_authenticated:
        return JsonResponse({"error": "login_required"}, status=401)

    if request.POST.get("allow") != "true":
        # Denied: authlib produces the standard access_denied redirect.
        return server.create_authorization_response(request, grant_user=None)

    # The organization *id*, not its slug: this choice decides which tenant every
    # token minted from this authorization is scoped to, so it is keyed on the row
    # rather than on a mutable, user-chosen handle. A malformed value fails closed
    # as a 400 rather than surfacing as a 500.
    organization = request.POST.get("organization")
    try:
        membership = Membership.objects.get(user=request.user, organization_id=organization)
    except (Membership.DoesNotExist, ValueError, ValidationError):
        return JsonResponse(
            {"error": "invalid_request", "error_description": "You are not a member of the selected organization."},
            status=400,
        )

    return server.create_authorization_response(request, grant_user=membership)


@csrf_exempt
@require_http_methods(["POST"])
def revoke_token(request: HttpRequest) -> HttpResponse:
    """RFC 7009 token revocation endpoint (POST only).

    Sets ``OAuth2Token.revoked``, which severs the refresh chain immediately;
    the short-lived JWT access token ages out on its own.
    """
    try:
        return server.create_endpoint_response("revocation", request)
    except OAuth2Error as error:
        return server.handle_response(*error())


@csrf_exempt
@require_http_methods(["POST"])  # we only allow POST for token endpoint
def issue_token(request: HttpRequest) -> HttpResponse | tuple:
    """Token endpoint (POST only).

    This view delegates the heavy lifting to the AuthorizationServer
    instance exported from :mod:`authapp.server` via
    ``server.create_token_response`` which returns a Django
    HttpResponse appropriate for RFC-compliant token responses.

    Security notes:
    - The endpoint is explicitly CSRF-exempt because OAuth token
      requests are typically made by non-browser clients.
    - Only POST requests are allowed.

    Args:
        request: Django HttpRequest containing the token request data.

    Returns:
        HttpResponse produced by the AuthorizationServer token handler.
    """
    from authapp.throttle import TOKEN_LIMIT_PER_MINUTE, is_throttled, throttled_response

    if is_throttled(request, "token", TOKEN_LIMIT_PER_MINUTE):
        return throttled_response()

    try:
        return server.create_token_response(request)
    except OAuth2Error as error:
        return server.handle_response(*error())


class CustomLoginView(LoginView):
    """Login view configured for the project's tailwind-based template.

    Behavior:
    - Uses the 'login.html' template by default.
    - Redirects authenticated users away from the login page.
    - Falls back to the named URL 'home' after successful login.
    """

    template_name = "login.html"  # your Tailwind template
    redirect_authenticated_user = True  # Redirect if already logged in
    success_url = reverse_lazy("home")  # Replace 'home' with your view name

    def get_success_url(self) -> str | None:
        """Return the URL to redirect to after successful login.

        The method prefers a ``next`` redirect parameter when present and
        otherwise falls back to the configured ``success_url``.
        """
        # This uses ?next=... if present; otherwise falls back to success_url
        return self.get_redirect_url() or self.success_url


def logout_view(request: HttpRequest) -> HttpResponse:
    """Log out the current user and redirect to the login page.

    Args:
        request: Django HttpRequest

    Returns:
        HttpResponse redirecting to the 'login' named URL.
    """
    logout(request)
    return redirect("login")


@login_required
def home_view(request: HttpRequest) -> HttpResponse:
    """Simple authenticated home page.

    Renders 'home.html' with the currently authenticated user available
    in the template context as ``user``.
    """
    return render(request, "home.html", {"user": request.user})
