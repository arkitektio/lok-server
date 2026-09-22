"""Typed, fully-documented configuration schema for the **lok** service.

Owned by this service. Values resolve (highest precedence first) from init
kwargs, environment variables (nested via ``__`` — e.g. ``POSTGRES__PASSWORD``),
then the YAML file (the mount's ``config.yaml`` by default; override with
``ARKITEKT_CONFIG_FILE``). Secret fields have **no default**: loading fails fast
with a ``ValidationError`` if they are not supplied via config or environment.
"""

import hashlib
import warnings
import os
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from authentikate.base_models import AuthentikateSettings

_DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")


class AdminSettings(BaseModel):
    """Django superuser created on first boot."""

    username: str = Field(description="Superuser login name.")
    password: str = Field(description="Superuser password. Secret — must be set.")
    email: Optional[str] = Field(default=None, description="Superuser email address.")


class DjangoSettings(BaseModel):
    """Core Django framework settings."""

    secret_key: str = Field(description="Django SECRET_KEY for cryptographic signing. Secret — must be set.")
    debug: bool = Field(default=False, description="Enable Django debug mode (never in production).")
    hosts: List[str] = Field(default_factory=lambda: ["*"], description="ALLOWED_HOSTS entries.")
    use_x_forwarded_host: bool = Field(default=True, description="Trust the X-Forwarded-Host header behind a reverse proxy.")
    secure_proxy_ssl_header: bool = Field(default=True, description="Trust X-Forwarded-Proto to detect HTTPS behind a reverse proxy (SECURE_PROXY_SSL_HEADER). Disable when not behind a TLS-terminating proxy.")
    allow_insecure_transport: bool = Field(
        default=False,
        description=(
            "Let OAuth2/OIDC endpoints (token, authorize, discovery) accept plain-HTTP "
            "requests. authlib otherwise rejects non-https, non-localhost URLs with "
            "InsecureTransportError. Enable for deployments that deliberately run lok "
            "without TLS, or behind a proxy that does not forward X-Forwarded-Proto. "
            "Equivalent to exporting AUTHLIB_INSECURE_TRANSPORT=1."
        ),
    )
    cors_allowed_origins: List[str] = Field(
        default_factory=list,
        description=(
            "Browser origins (scheme://host[:port]) allowed to call the OAuth/OIDC "
            "(/o/), fakts (/f/) and discovery (/.well-known/) endpoints cross-origin "
            "(django-cors-headers CORS_ALLOWED_ORIGINS). Empty means no cross-origin "
            "browser access unless cors_allow_all_origins is set."
        ),
    )
    cors_allow_all_origins: bool = Field(
        default=False,
        description=(
            "Answer every Origin with Access-Control-Allow-Origin on the OAuth/fakts/"
            "discovery endpoints (CORS_ALLOW_ALL_ORIGINS). Those endpoints are public "
            "and bearer-authenticated (no cookies are sent cross-origin), so this is "
            "safe for deployments whose web apps live on arbitrary hosts; prefer an "
            "explicit cors_allowed_origins list when the hosts are known."
        ),
    )
    trusted_proxy_depth: int = Field(
        default=0,
        ge=0,
        description=(
            "How many reverse proxies you control sit in front of lok. Per-IP rate "
            "limiting reads the client address this many entries from the RIGHT of "
            "X-Forwarded-For, so a client-supplied prefix cannot forge it. 0 means "
            "no trusted proxy: X-Forwarded-For is ignored entirely and REMOTE_ADDR "
            "is used. Set to 1 behind a single gateway (Caddy/nginx/ELB)."
        ),
    )
    secure_cookies: Optional[bool] = Field(
        default=None,
        description=(
            "Mark session and CSRF cookies Secure (HTTPS-only). Defaults to the "
            "opposite of allow_insecure_transport, i.e. on unless the deployment "
            "deliberately runs without TLS."
        ),
    )
    hsts_seconds: int = Field(
        default=31536000,
        ge=0,
        description=(
            "SECURE_HSTS_SECONDS. 0 disables HSTS. Ignored when the deployment has "
            "opted into plain HTTP via allow_insecure_transport."
        ),
    )
    admin: AdminSettings = Field(description="Superuser provisioned on first boot.")
    csrf_trusted_origins: List[str] = Field(default_factory=lambda: ["http://localhost", "https://localhost"], description="CSRF_TRUSTED_ORIGINS for unsafe (POST) requests.")
    force_script_name: str = Field(default="", description="URL path prefix (FORCE_SCRIPT_NAME) this service is served under.")
    language_code: str = Field(default="en-us", description="Django LANGUAGE_CODE.")
    time_zone: str = Field(default="UTC", description="Django TIME_ZONE.")
    log_level: str = Field(default="INFO", description="Root logger level (e.g. DEBUG, INFO, WARNING). The LOG_LEVEL env var overrides it.")
    enable_rich_logging: bool = Field(default=False, description="Render console logs with rich (colours, boxed tracebacks). A dev convenience; off by default, as plain one-line records suit container logs.")


class PostgresSettings(BaseModel):
    """PostgreSQL database connection (Django ``DATABASES['default']``)."""

    model_config = ConfigDict(extra="allow")

    engine: str = Field(default="django.db.backends.postgresql", description="Django database backend (PostgreSQL).")
    db_name: str = Field(description="Database name.")
    username: str = Field(description="Database user.")
    password: str = Field(description="Database password. Secret — must be set.")
    host: str = Field(description="Database host.")
    port: int = Field(default=5432, description="Database port.")


class RedisSettings(BaseModel):
    """Redis connection (channel layer / cache)."""

    model_config = ConfigDict(extra="allow")

    host: str = Field(description="Redis host.")
    port: int = Field(default=6379, description="Redis port.")
    channel_prefix: str = Field(default="lok", description="Key prefix for the channels_redis channel layer.")


class LokSettings(BaseModel):
    """Lok identity-provider key material used by this service."""

    public_key: Optional[str] = Field(default=None, description="Lok public key (SSH/PEM) used to verify issued tokens.")
    key_id: str = Field(default="lok-key-1", description="JWK `kid` advertised in the JWKS and stamped into issued token headers. Must match the kid configured for the lok issuer in consumers' authentikate settings.")
    static_tokens: Dict[str, Any] = Field(default_factory=dict, description="Pre-shared static tokens (testing only).")


class EmailSettings(BaseModel):
    """SMTP settings for outbound email (optional block)."""

    host: str = Field(default="NOTSET", description="SMTP server host.")
    port: int = Field(default=587, description="SMTP server port.")
    use_tls: bool = Field(default=True, description="Use STARTTLS.")
    user: str = Field(default="NOTSET", description="SMTP username.")
    password: str = Field(description="SMTP password. Secret — must be set when an email block is present.")
    email: str = Field(default="NOTSET", description="Default From address.")


class DeploymentSettings(BaseModel):
    """Human-facing deployment identity."""

    name: str = Field(default="default", description="Deployment name.")
    description: str = Field(default="A Basic Arkitekt Deployment", description="Deployment description.")
    configure_url: str = Field(
        default="/configure/{code}",
        description="URL template the fakts well-known advertises as the device-code "
        "`configure` endpoint; the literal `{code}` placeholder is substituted by the "
        "client with the device code. A root-relative path (`/configure/{code}`) is "
        "resolved against the deployment's base domain; a value carrying a scheme "
        "(`https://…`) is used verbatim; a bare host (`go.arkitekt.live/configure/{code}`) "
        "is treated as https. The well-known always advertises the resolved *absolute* URL.",
    )
    mesh_configure_url: str = Field(
        default="/meshconfigure/{code}",
        description="URL template the fakts well-known advertises as the *mesh* device-code "
        "`mesh_configure` endpoint. Resolved to an absolute URL the same way as "
        "`configure_url`; the literal `{code}` placeholder is substituted by the machine "
        "with the mesh device code.",
    )
    hub_configure_url: str = Field(
        default="/hubconfigure/{code}",
        description="URL template the fakts well-known advertises as the *hub* "
        "device-code `hub_configure` endpoint. Resolved to an absolute URL the "
        "same way as `configure_url`; the literal `{code}` placeholder is substituted by "
        "the client with the hub device code.",
    )


class IonscaleSettings(BaseModel):
    """Connection to an ionscale tailnet coordinator (optional block)."""

    model_config = ConfigDict(extra="allow")

    server_url: str = Field(description="Ionscale server URL.")
    service_token: Optional[str] = Field(
        default=None,
        description="Static service token (`svc_…`) declared under `auth.service_tokens` in the "
        "ionscale config. Secret. Lok talks to ionscale over HTTP with it; preferred over `admin_key`.",
    )
    admin_key: Optional[str] = Field(
        default=None,
        description="Legacy: ionscale system admin key, used to drive the `ionscale` CLI binary. "
        "Secret. Only needed when `service_token` is not set.",
    )
    coord_url: str = Field(description="Public coordination URL advertised to clients.")
    verify_tls: bool = Field(default=True, description="Verify ionscale's TLS certificate (HTTP mode).")
    timeout: float = Field(default=10.0, description="Per-request timeout in seconds (HTTP mode).")
    magic_dns_suffix: Optional[str] = Field(
        default=None,
        description="MagicDNS suffix served by ionscale (mirrors its dns.magic_dns_suffix). "
        "Used to derive a machine's MagicDNS name as `<name>.<suffix>`.",
    )
    repository: Optional[str] = Field(default=None, description="Dotted path to an IonscaleRepo factory (tests).")
    eager_init: bool = Field(default=False, description="Eagerly initialize the ionscale repo on boot (tests).")
    auto_create_mesh: bool = Field(
        default=True,
        description="Automatically provision the mesh for each new organization on creation. "
        "Requires ionscale to be configured; when disabled, meshes are only created on explicit opt-in.",
    )

    @model_validator(mode="after")
    def _require_a_credential(self) -> "IonscaleSettings":
        if not (self.service_token or self.admin_key or self.repository):
            raise ValueError("ionscale: set `service_token` (preferred) or the legacy `admin_key`")
        return self


class DatalayerBucket(BaseModel):
    """A single S3 bucket binding within the datalayer."""

    model_config = ConfigDict(extra="allow")

    bucket: str = Field(description="S3 bucket name.")


class DatalayerSettings(BaseModel):
    """S3 storage connection and buckets (the datalayer module; replaces the old top-level ``s3`` block)."""

    model_config = ConfigDict(extra="allow")

    access_key: str = Field(description="S3 access key. Secret — must be set.")
    secret_key: str = Field(description="S3 secret key. Secret — must be set.")
    host: Optional[str] = Field(default=None, description="S3 endpoint host.")
    port: Optional[int] = Field(default=None, description="S3 endpoint port.")
    protocol: str = Field(default="http", description="S3 endpoint protocol (http or https).")
    region: str = Field(default="us-east-1", description="S3 region name.")
    default_acl: str = Field(default="private", description="Default ACL applied to stored objects (AWS_DEFAULT_ACL).")
    querystring_expire: int = Field(default=3600, description="Presigned URL lifetime in seconds (AWS_QUERYSTRING_EXPIRE).")
    file_overwrite: bool = Field(default=False, description="Overwrite existing files on name collision (AWS_S3_FILE_OVERWRITE).")
    secure: Optional[bool] = Field(default=None, description="Use TLS for S3 (AWS_S3_USE_SSL/SECURE_URLS). When None, derived from protocol == 'https'.")
    media: DatalayerBucket = Field(description="Bucket for media / general file storage. Required for this service.")
    zarr: Optional[DatalayerBucket] = Field(default=None, description="Bucket for Zarr arrays.")
    parquet: Optional[DatalayerBucket] = Field(default=None, description="Bucket for Parquet tables.")
    bigfile: Optional[DatalayerBucket] = Field(default=None, description="Bucket for large binary files.")


class HeadlessFrontendUrls(BaseModel):
    """Single-page-app URLs allauth-headless points users at (the ``{key}`` placeholders are filled in by allauth).

    These are **relative path templates** by default; they are joined to
    ``kontrol_frontend_url`` in ``settings.py`` so the SPA host is configured in one
    place. Set a field to a fully-qualified URL (with scheme) to override a single
    flow's host independently of the base.
    """

    account_confirm_email: str = Field(
        default="/account/verify-email/{key}",
        description="Email-verification link; {key} substituted by allauth. Joined to kontrol_frontend_url unless absolute.",
    )
    account_reset_password: str = Field(
        default="/account/password/reset",
        description="Password-reset request page. Joined to kontrol_frontend_url unless absolute.",
    )
    account_reset_password_from_key: str = Field(
        default="/account/password/reset/key/{key}",
        description="Password-reset-from-key link; {key} substituted by allauth. Joined to kontrol_frontend_url unless absolute.",
    )
    account_signup: str = Field(
        default="/account/signup",
        description="Signup page URL. Joined to kontrol_frontend_url unless absolute.",
    )


class AccountSettings(BaseModel):
    """django-allauth account/MFA behavior."""

    email_verification: str = Field(default="none", description="ACCOUNT_EMAIL_VERIFICATION (none/optional/mandatory).")
    social_email_verification: Optional[Literal["none", "optional", "mandatory"]] = Field(
        default=None,
        description="SOCIALACCOUNT_EMAIL_VERIFICATION — email-verification policy for logins via a "
        "social/OIDC provider, evaluated independently of `email_verification` (which governs "
        "local email/password accounts). None (default) inherits `email_verification`. Set to "
        "'none' to admit provider-authenticated users (Google, ORCID, CILogon, …) without our own "
        "email confirmation — appropriate when the IdP is trusted to vouch for identity, and "
        "required for providers such as ORCID that may release no email at all. This is the "
        "supported way to say 'local signups must verify, IdP logins need not'.",
    )
    social_email_required: Optional[bool] = Field(
        default=None,
        description="SOCIALACCOUNT_EMAIL_REQUIRED — whether an email address must be present to "
        "sign up via a social provider. None (default) inherits allauth's derivation from "
        "signup_fields. Set False to admit providers that don't release an email (e.g. ORCID).",
    )
    login_methods: List[Literal["username", "email", "phone"]] = Field(
        default_factory=lambda: ["username"],
        description="ACCOUNT_LOGIN_METHODS — the identifier(s) users may log in with. "
        "Set to ['email'] to enable login via email.",
    )
    signup_fields: Optional[List[str]] = Field(
        default=None,
        description="ACCOUNT_SIGNUP_FIELDS, e.g. ['email*', 'password1*', 'password2*'] "
        "('*' marks a required field). When null, derived from login_methods.",
    )
    login_by_code_enabled: bool = Field(default=True, description="Enable login by emailed code (ACCOUNT_LOGIN_BY_CODE_ENABLED).")
    mfa_trust_enabled: bool = Field(default=True, description="Allow trusted devices (MFA_TRUST_ENABLED).")
    mfa_webauthn_enabled: bool = Field(
        default=True,
        description="Offer WebAuthn security keys/passkeys as an MFA type (adds 'webauthn' to "
        "MFA_SUPPORTED_TYPES). Browsers only expose the WebAuthn API on a secure origin, but that "
        "is judged against the page's own origin — not lok's transport — so this is NOT gated on "
        "allow_insecure_transport, which is normal behind a TLS-terminating gateway.",
    )
    mfa_passkey_login_enabled: bool = Field(
        default=True,
        description="MFA_PASSKEY_LOGIN_ENABLED — let a passkey replace the password at login. "
        "Requires mfa_webauthn_enabled; allauth gates it on 'webauthn' being a supported type.",
    )
    mfa_passkey_signup_enabled: bool = Field(
        default=False,
        description="MFA_PASSKEY_SIGNUP_ENABLED — allow signing UP with a passkey and no password. "
        "Requires email_verification_by_code_enabled: allauth raises a Critical system check "
        "otherwise, because a passwordless signup has no way to prove the address without a code.",
    )
    email_verification_by_code_enabled: bool = Field(
        default=False,
        description="ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED — verify email addresses with a "
        "short code the user types in, instead of a confirmation link they click. This changes "
        "verification for EVERY signup, not just passkey ones, and makes the "
        "headless_frontend_urls.account_confirm_email link unused. Required by "
        "mfa_passkey_signup_enabled.",
    )
    mfa_webauthn_rp_id: Optional[str] = Field(
        default=None,
        description="WebAuthn Relying Party ID — the domain a passkey is bound to. When null, "
        "allauth derives it per request from the Host header, which scopes each credential to a "
        "single hostname. Pin it to the registrable parent domain (e.g. 'arkitekt.live') when one "
        "deployment answers on several subdomains, or a passkey enrolled on one will not be "
        "offered on another. Must equal, or be a registrable-domain suffix of, every host the SPA "
        "is served from.",
    )
    mfa_totp_issuer: Optional[str] = Field(
        default=None,
        description="MFA_TOTP_ISSUER — the label authenticator apps show next to the code. "
        "allauth defaults to an empty string, which leaves the entry unlabelled and unfindable "
        "among a user's other accounts. Defaults to the deployment name when null.",
    )
    headless_frontend_urls: HeadlessFrontendUrls = Field(default_factory=HeadlessFrontendUrls, description="SPA URLs for allauth-headless flows.")
    social_provider_apps: List[str] = Field(
        default_factory=lambda: ["allauth.socialaccount.providers.orcid", "allauth.socialaccount.providers.google"],
        description="allauth social provider apps appended to INSTALLED_APPS.",
    )

    @model_validator(mode="after")
    def _coherent_login_and_signup(self) -> "AccountSettings":
        """Keep login_methods and signup_fields coherent.

        When signup_fields is omitted, derive a sensible default from
        login_methods. When it is provided, ensure an email login isn't paired
        with a signup form that never asks for an email.
        """
        if self.signup_fields is None:
            fields: List[str] = []
            if "email" in self.login_methods:
                fields.append("email*")
            if "username" in self.login_methods:
                fields.append("username*")
            if "phone" in self.login_methods:
                fields.append("phone*")
            fields += ["password1*", "password2*"]
            self.signup_fields = fields
        elif "email" in self.login_methods and not any(
            f.rstrip("*") == "email" for f in self.signup_fields
        ):
            raise ValueError(
                "account.login_methods includes 'email' but account.signup_fields "
                "does not collect an email — add 'email*' so users can sign up."
            )
        return self

    @model_validator(mode="after")
    def _coherent_social_email(self) -> "AccountSettings":
        """Reject a self-contradictory social email policy.

        Mandatory verification for social logins means allauth tries to send a
        confirmation to the provider's email and blocks until confirmed. If email
        is simultaneously declared *not required* (``social_email_required: false``)
        there may be no address to send to — the login would dead-end. Requiring an
        email is a precondition for being able to verify it."""
        if self.social_email_verification == "mandatory" and self.social_email_required is False:
            raise ValueError(
                "account.social_email_verification is 'mandatory' but "
                "account.social_email_required is false — verification needs an email "
                "to confirm, so a social login with no email could never complete. Set "
                "social_email_required to true, or relax social_email_verification."
            )
        return self

    @model_validator(mode="after")
    def _coherent_mfa_webauthn(self) -> "AccountSettings":
        """Reject passkey flags allauth would ignore or refuse to boot with.

        allauth ANDs both passkey flags with "webauthn" being in
        MFA_SUPPORTED_TYPES (allauth/mfa/app_settings.py:102-110), so enabling
        one without the type silently does nothing. And passkey *signup* is a
        Critical system check failure unless email verification is code-based
        (allauth/mfa/checks.py:20-26) — the account has no password, so a
        clickable confirmation link is not enough to complete it. Fail here, with
        the reason, rather than at boot with a Django check error.
        """
        if self.mfa_passkey_login_enabled and not self.mfa_webauthn_enabled:
            raise ValueError(
                "account.mfa_passkey_login_enabled requires account.mfa_webauthn_enabled — "
                "allauth ignores the passkey-login flag unless 'webauthn' is a supported MFA type."
            )
        if self.mfa_passkey_signup_enabled and not self.mfa_webauthn_enabled:
            raise ValueError(
                "account.mfa_passkey_signup_enabled requires account.mfa_webauthn_enabled — "
                "allauth ignores the passkey-signup flag unless 'webauthn' is a supported MFA type."
            )
        if self.mfa_passkey_signup_enabled and not self.email_verification_by_code_enabled:
            raise ValueError(
                "account.mfa_passkey_signup_enabled requires "
                "account.email_verification_by_code_enabled — allauth refuses to start otherwise "
                "(MFA_PASSKEY_SIGNUP_ENABLED needs ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED). "
                "Note that turning it on switches email verification from links to codes for "
                "every signup, not only passkey ones."
            )
        return self


class OpenIDAppSettings(BaseModel):
    """An OIDC/OAuth2 client provisioned on boot (see the ``ensureopenid`` command)."""

    client_name: str = Field(description="Human-readable client name.")
    client_id: str = Field(description="OAuth2 client_id.")
    client_secret: str = Field(description="OAuth2 client secret. Override per deployment.")
    redirect_uris: List[str] = Field(default_factory=list, description="Allowed OAuth2 redirect URIs.")
    email_template: Optional[str] = Field(
        default=None,
        description="Template for the `email` claim, rendered per user from membership variables "
        "(e.g. '{username}@corp.example'). Available variables: username, user_id, email, "
        "membership_id, org_slug, org_name. When unset, the user's own email is used (falling "
        "back to a synthetic <pk>@users.noreply address).",
    )

    scope: str = Field(
        default="openid profile email",
        description="Space-separated scopes this relying party may request. Claims are gated "
        "per scope (OIDC Core §5.4): `profile` buys the name claims, `email` the address, and "
        "`openid` alone buys only `sub`/`org`. Narrow this for a relying party that needs "
        "nothing but identity.",
    )
    require_nonce: bool = Field(
        default=False,
        description="Reject an authorization request from this client that carries no `nonce`. "
        "OIDC Core §3.1.2.1 makes `nonce` OPTIONAL for the code flow and no discovery field "
        "can advertise a stricter rule, so this must be agreed with the relying party out of "
        "band. Off by default.",
    )

    @field_validator("scope")
    @classmethod
    def _validate_scope(cls, value: str) -> str:
        scopes = value.split()
        if "openid" not in scopes:
            raise ValueError("`scope` must include 'openid' for an OIDC relying party.")
        unknown = sorted(set(scopes) - {"openid", "profile", "email"})
        if unknown:
            raise ValueError(
                f"`scope` contains scopes lok does not advertise: {', '.join(unknown)}. "
                "Supported: openid, profile, email."
            )
        return " ".join(scopes)

    @field_validator("email_template")
    @classmethod
    def _validate_email_template(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            # Pure (stdlib-only) validator shared with the runtime renderer.
            from authapp.oidc_claims import validate_email_template

            validate_email_template(value)
        return value


class SocialAppConfig(BaseModel):
    """Credentials for one OAuth app of a social provider.

    Mirrors the ``APP`` dict django-allauth reads from
    ``SOCIALACCOUNT_PROVIDERS[<provider>]["APP"]`` (or each entry of ``APPS``);
    the field names match allauth's exactly (see
    ``allauth/socialaccount/adapter.py``).
    """

    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(description="OAuth client id / consumer key issued by the provider.")
    secret: str = Field(default="", description="OAuth client secret. Secret — set per deployment.")
    key: str = Field(default="", description="Extra key a few providers require; usually blank.")
    name: Optional[str] = Field(default=None, description="Human-readable app name (defaults to the provider id).")
    provider_id: Optional[str] = Field(default=None, description="Sub-provider instance id (OpenID Connect / SAML).")
    settings: Dict[str, Any] = Field(default_factory=dict, description="Provider-app-specific settings blob.")


class SocialProviderConfig(BaseModel):
    """One entry of ``SOCIALACCOUNT_PROVIDERS``, keyed by provider id (e.g. ``google``).

    The common keys are typed for editor help and validation; any
    provider-specific extras (e.g. ``FETCH_USERINFO``) are still accepted
    verbatim via ``extra="allow"``.
    """

    model_config = ConfigDict(extra="allow")

    APP: Optional[SocialAppConfig] = Field(default=None, description="A single OAuth app credential.")
    APPS: Optional[List[SocialAppConfig]] = Field(default=None, description="Multiple app credentials (rarely needed; use APP for one).")
    SCOPE: Optional[List[str]] = Field(default=None, description="OAuth scopes to request (e.g. ['profile', 'email']).")
    AUTH_PARAMS: Optional[Dict[str, Any]] = Field(default=None, description="Extra query params on the authorize request.")
    OAUTH_PKCE_ENABLED: Optional[bool] = Field(default=None, description="Enable PKCE where the provider supports it (recommended).")
    VERIFIED_EMAIL: Optional[bool] = Field(default=None, description="Treat emails from this provider as already verified.")
    EMAIL_AUTHENTICATION: Optional[bool] = Field(default=None, description="Match logins to existing accounts by email.")


class Settings(BaseSettings):
    """Top-level, validated configuration for the lok service."""

    model_config = SettingsConfigDict(env_nested_delimiter="__", extra="ignore")

    django: DjangoSettings = Field(description="Core Django settings.")
    postgres: PostgresSettings = Field(description="PostgreSQL connection.")
    redis: RedisSettings = Field(description="Redis connection.")
    lok: LokSettings = Field(default_factory=LokSettings, description="Lok IdP key material.")
    authentikate: AuthentikateSettings = Field(description="Token-verification config (authentikate).")
    datalayer: DatalayerSettings = Field(description="S3 storage connection and buckets.")
    deployment: DeploymentSettings = Field(default_factory=DeploymentSettings, description="Deployment identity.")
    account: AccountSettings = Field(default_factory=AccountSettings, description="django-allauth account/MFA behavior.")
    email: Optional[EmailSettings] = Field(default=None, description="Optional SMTP settings for outbound email.")
    ionscale: Optional[IonscaleSettings] = Field(default=None, description="Optional ionscale coordinator connection.")
    private_key: str = Field(description="OIDC/OAuth2 RSA private signing key (PEM). Secret — must be set.")
    oidc_issuer: str = Field(default="http://lok", description="OIDC issuer URL advertised by lok.")
    kontrol_frontend_url: str = Field(
        default="/",
        description="Base URL of the kontrol SPA. All account email links (verify-email, "
        "password reset, signup) and karakter view redirects (invites, organizations) derive "
        "from it. Set to the deployment's frontend origin, e.g. https://go.arkitekt.live. "
        "The default '/' keeps links same-origin for local/dev.",
    )
    privacy_guards: Literal["strict", "opt-in", "disabled"] = Field(
        default="opt-in",
        description="Policy for integrated login widgets (e.g. Google One Tap) that load "
        "third-party scripts and can identify/track the visitor before any click. "
        "'strict' = never load; 'opt-in' = show a consent prompt first (default, matches "
        "the SPA's current behavior); 'disabled' = load freely with no prompt. Reported "
        "verbatim on the allauth headless /config endpoint so the SPA gates the widget.",
    )
    socialaccount_providers: Dict[str, SocialProviderConfig] = Field(
        default_factory=dict,
        description="django-allauth SOCIALACCOUNT_PROVIDERS, keyed by provider id (e.g. 'google'). "
        "The matching app must also be listed in account.social_provider_apps.",
    )
    organizations: List[Dict[str, Any]] = Field(default_factory=list, description="Organizations ensured on boot.")
    users: List[Dict[str, Any]] = Field(default_factory=list, description="Users ensured on boot.")
    memberships: List[Dict[str, Any]] = Field(default_factory=list, description="User/organization memberships ensured on boot.")
    redeem_tokens: List[Dict[str, Any]] = Field(default_factory=list, description="Redeem tokens provisioned on boot.")
    kommunity_partners: List[Dict[str, Any]] = Field(default_factory=list, description="Pre-authorized kommunity partner apps.")
    system_messages: List[Dict[str, Any]] = Field(default_factory=list, description="System messages shown to users.")
    openid_apps: List[OpenIDAppSettings] = Field(default_factory=list, description="OIDC/OAuth2 clients provisioned on boot. Provided per deployment.")

    @model_validator(mode="after")
    def _refuse_committed_key_material_in_production(self) -> "Settings":
        """Refuse to boot on the key material committed to this repository.

        The repo's `config.yaml` is the *default* config source (see
        `_DEFAULT_CONFIG`), and it is tracked in git despite being listed in
        `.gitignore`, so it also ships inside the Docker image. It contains a real
        RSA private key and a real `SECRET_KEY`, not placeholders. A deployment
        that forgets to mount its own config therefore signs OIDC id_tokens with a
        key anyone can read out of the repository — which is total: an attacker
        can mint a valid token for any user.

        `SECRET_KEY` matters beyond signing too: `karakter.hashers.hash_device_id`
        uses it as the HMAC pepper for device-id pseudonymisation.

        Compared by digest rather than by embedding the secrets again here.

        The bypass used to be `if self.django.debug: return self` — but the
        shipped `config.yaml` *itself* sets `debug: true`, so the guard never
        fired on the exact configuration it exists to catch: `docker run` with no
        config mounted booted happily on the public signing key. The opt-out is
        now an explicit, deliberate environment variable, so running on committed
        key material is something you have to *say*, not something you inherit.
        """
        if os.environ.get("LOK_ALLOW_COMMITTED_KEYS") == "1":
            return self

        compromised = {
            "c01028cb2381fe78d0fbba311f380e3a5af3c7a03e231b5578dfc2a6fe46a14d": "private_key",
            "56846ede21a4ab7340e217152f784522843bef23c10f834703ed00b6a7a038b6": "django.secret_key",
        }

        offenders = [
            name
            for value, name in (
                (self.private_key, "private_key"),
                (self.django.secret_key, "django.secret_key"),
            )
            if compromised.get(hashlib.sha256(value.strip().encode()).hexdigest()) == name
        ]

        # The rest of the committed credentials. These are *warned* about rather
        # than refused: unlike the signing secrets they cannot be used to forge a
        # token, and failing the boot on them would strand any deployment whose
        # database already uses the shipped password. Loud, not fatal.
        stale = [
            name
            for value, name, digest in (
                (self.postgres.password, "postgres.password",
                 "f1421261b9b60d46855115e9e96b9f976fbbfc093943785537c90d1a3ae13cc1"),
                (self.django.admin.password, "django.admin.password",
                 "8c6976e5b5410415bde908bd4dee15dfb167a9c873fc4bb8a81f6f2ab448a918"),
            )
            if value and hashlib.sha256(value.strip().encode()).hexdigest() == digest
        ]
        if stale and not self.django.debug:
            warnings.warn(
                "Running with credentials from the repository's committed config.yaml "
                f"({', '.join(stale)}). These values are public — rotate them.",
                stacklevel=2,
            )

        if offenders:
            raise ValueError(
                "Refusing to start with key material from the repository's committed "
                f"config.yaml ({', '.join(offenders)}). These values are public — anyone "
                "with the repo can forge tokens. Generate fresh values and supply them "
                "via your deployment config or environment (PRIVATE_KEY, DJANGO__SECRET_KEY). "
                "Set LOK_ALLOW_COMMITTED_KEYS=1 to bypass this deliberately (local development only)."
            )

        return self

    @model_validator(mode="after")
    def _refuse_static_tokens_in_production(self) -> "Settings":
        """Static tokens bypass signature verification entirely.

        `authentikate.utils.authenticate_token` short-circuits on
        `static_tokens[token]`, and `StaticToken` defaults to `roles: ["admin"]`.
        The library calls it "testing only", but it was surfaced as an ordinary
        deployment config key sitting in the same block as the issuer list, with
        nothing tying it to DEBUG — one copy-paste from an unauthenticated admin
        principal in production.
        """
        if self.django.debug:
            return self
        if self.lok.static_tokens:
            raise ValueError(
                "authentikate.static_tokens / lok.static_tokens bypass signature "
                "verification and are for tests only. Refusing to start with them "
                "configured while debug is off."
            )
        return self

    @model_validator(mode="after")
    def _mandatory_verification_needs_smtp(self) -> "Settings":
        """Mandatory email verification blocks *all* post-signup login, so it
        requires a working outbound mail path — fail fast if the SMTP block is
        missing rather than locking users out at runtime.

        Note: ``account.login_by_code_enabled`` has the same email dependency but
        is not hard-failed here — password login still works without it. Instead it
        is soft-disabled (effective value ``False``) when no ``email:`` block is
        present; see ``ACCOUNT_LOGIN_BY_CODE_ENABLED`` in settings.py and CONFIG.md.

        The same SMTP dependency applies to ``account.social_email_verification``
        when it is set to ``mandatory``."""
        mandatory = [
            name
            for name, value in (
                ("email_verification", self.account.email_verification),
                ("social_email_verification", self.account.social_email_verification),
            )
            if value == "mandatory"
        ]
        if mandatory and self.email is None:
            raise ValueError(
                f"account.{' and account.'.join(mandatory)} is 'mandatory' but no "
                "`email:` SMTP block is configured — verification mails can't be sent "
                "and affected users would be permanently locked out. Add an `email:` "
                "block or relax verification."
            )
        return self

    @model_validator(mode="after")
    def _relaxed_social_verification_needs_trusted_providers(self) -> "Settings":
        """If social logins are exempted from email verification, every configured
        social provider must be explicitly trusted.

        ``social_email_verification: none`` opens the login gate for *all* social
        providers globally (allauth has no per-provider verification level). Leaving
        a provider untrusted then means its users are admitted *and* their email is
        stored unverified with no path to ever verify it — a silent, unintended
        exemption, especially for a provider added later. Requiring an explicit trust
        declaration makes that decision deliberate, matching the intent behind the
        global switch.

        Trust can be declared at two levels, mirroring allauth's own lookup order in
        ``SocialAccountAdapter.is_email_verified``: per *app*, via
        ``settings.verified_email`` on each entry, and per *provider*, via the
        top-level ``VERIFIED_EMAIL``. Either may be a bool or a list of domains.

        Two kinds of provider must instead give **every app a non-empty domain list**,
        because for them neither a provider-wide flag nor a per-app bare ``true`` is
        expressive enough — both say "believe this IdP about any address at all", which
        lets it mark a domain it does not own as verified:

        * **SAML**, at any app count. Its apps are institutional identity providers, and
          an institution has no business asserting addresses outside its own domains.
        * **Any provider with several apps**, since one flag cannot distinguish between
          independent identity providers and would silently cover the next app added.

        Single-app OAuth providers (google, orcid, cilogon) are unaffected and keep
        using a provider-wide ``VERIFIED_EMAIL``.

        Note allauth matches those domains **exactly**, not by suffix: an app scoped to
        ``acme.edu`` does not cover ``student.acme.edu``. Enumerate every subdomain the
        IdP actually asserts."""
        if self.account.social_email_verification != "none":
            return self

        def apps_of(cfg: SocialProviderConfig) -> List[SocialAppConfig]:
            apps = list(cfg.APPS or [])
            if cfg.APP is not None:
                apps.append(cfg.APP)
            return apps

        def is_domain_list(value: Any) -> bool:
            return isinstance(value, list) and len(value) > 0

        untrusted: List[str] = []
        needs_domain_list: List[str] = []

        for name, cfg in self.socialaccount_providers.items():
            apps = apps_of(cfg)
            per_app_values = [app.settings.get("verified_email") for app in apps]

            if name == "saml" or len(apps) > 1:
                # Only domain-scoped per-app trust is expressive enough here.
                if apps and all(is_domain_list(value) for value in per_app_values):
                    continue
                if any(per_app_values) or getattr(cfg, "VERIFIED_EMAIL", None):
                    needs_domain_list.append(name)
                else:
                    untrusted.append(name)
                continue

            if (apps and all(per_app_values)) or getattr(cfg, "VERIFIED_EMAIL", None):
                continue
            untrusted.append(name)

        if untrusted:
            raise ValueError(
                "account.social_email_verification is 'none' (social logins skip "
                "email verification), but these providers are not marked trusted: "
                f"{', '.join(sorted(untrusted))}. Add `VERIFIED_EMAIL: true` to each "
                "provider, or `settings.verified_email: [\"<domain>\"]` to each of its "
                "apps, so the exemption is an explicit trust decision — or raise "
                "social_email_verification to 'optional'/'mandatory'."
            )

        if needs_domain_list:
            raise ValueError(
                "Every app of these providers must declare `settings.verified_email` as "
                f"a non-empty list of domains: {', '.join(sorted(needs_domain_list))}. "
                "A provider-wide VERIFIED_EMAIL cannot express per-IdP trust, and a bare "
                "`verified_email: true` trusts an identity provider about *any* address "
                "— including domains it does not own. This is required for SAML at any "
                "app count, and for any provider configuring several apps. Use e.g. "
                "`verified_email: [\"acme.edu\", \"student.acme.edu\"]`; matching is exact, "
                "so enumerate every subdomain the IdP asserts."
            )

        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Precedence: explicit init kwargs > environment variables > YAML file.
        path = os.environ.get("ARKITEKT_CONFIG_FILE", _DEFAULT_CONFIG)
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=path),
            file_secret_settings,
        )
