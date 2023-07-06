from urllib.request import Request
from oauthlib import oauth2, common, openid
from oauthlib.oauth2.rfc6749 import tokens
from django.conf import settings
from django.contrib.auth.models import User
import random
import logging
from oauthlib.common import Request
from requests import request


def custom_token_generator(request, refresh_token=False):
    app = request.client
    user: User = request.user or app.user

    request.claims = {
        "type": app.authorization_grant_type,
        "tenant": app.user.email,
        "sub": user.id if user else None,
        "preferred_username": user.username if user else None,
        "roles": [group.name for group in user.groups.all()] if user else [],
        "scope": " ".join(request.scopes),
        "iss": "herre",
        "client_id": app.client_id,
        "version": app.client.release.version,
        "identifier": app.client.release.app.identifier,
    }
     
    return common.generate_signed_token(settings.OAUTH2_JWT["PRIVATE_KEY"], request)


class JWTServer(openid.Server):
    def __init__(
        self,
        request_validator,
        token_expires_in=None,
        token_generator=None,
        *args,
        **kwargs,
    ):
        token_generator = custom_token_generator
        super().__init__(
            request_validator, token_expires_in, token_generator, *args, **kwargs
        )

    def verify_request(
        self, uri, http_method="GET", body=None, headers=None, scopes=None
    ):
        """Validate client, code etc, return body + headers"""
        # FUCK THIS LIBRARY THIS IS JUST PURE MADNESS, WHY WOULD YOU MAKE IT JUSTDECIDE BY FEELING, IF ITS A BEARER OR IDTOKEN
        # TURNS OUT BEARER CAN ALSO BE JWTS YOU BASTEREDS
        request = Request(uri, http_method, body, headers)
        request.token_type = "bearer"
        request.scopes = scopes
        return self.bearer.validate_request(request), request

    def create_userinfo_response(self, *args, **kwargs):
        logging.error(f"OINSDFOINSÜEIUPNSPOEINFPSOEINFOSEINFOSEINFÜOESINF {args}")
        return super().create_userinfo_response(*args, **kwargs)
