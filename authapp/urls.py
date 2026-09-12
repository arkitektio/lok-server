# authapp/urls.py
from django.urls import path
from fakts.views import AppAuthorizationView, HubAuthorizationView
from .views import authorize, home_view, issue_token, jwks, revoke_token, user_info

urlpatterns = [
    path("home/", home_view, name="home"),
    path("token/", issue_token, name="token"),  # Token endpoint
    path("authorize/", authorize, name="authorize"),  # Authorization endpoint (consent via kontrol)
    path("revoke/", revoke_token, name="revoke"),  # RFC 7009 revocation
    # App authorization: RFC 8628 device authorization + dynamic client
    # registration — the canonical fakts grant's front door. Implemented in
    # fakts (the request body is a fakts manifest) but served under the OAuth
    # handle next to the token endpoint it pairs with.
    path("app-authorization/", AppAuthorizationView.as_view(), name="app_authorization"),
    # Same pattern for whole-hub provisioning.
    path("hub-authorization/", HubAuthorizationView.as_view(), name="hub_authorization"),
    path("jwks/", jwks, name="jwks"),  # JWKS endpoint
    path("user_info/", user_info, name="user_info"),  # User Info endpoint
]
