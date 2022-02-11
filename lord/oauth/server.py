from oauthlib import oauth2, common, openid
from oauthlib.oauth2.rfc6749 import tokens
from django.conf import settings
from django.contrib.auth.models import User
import random


def custom_token_generator(request, refresh_token=False):
    app = request.client
    user: User = request.user

    request.claims = {
        "type": app.authorization_grant_type,
        "email": user.email if user else None,
        "roles": [group.name for group in user.groups.all()] if user else [],
        "scope": " ".join(request.scopes),
        "iss": "herre",
        "client_id": app.client_id,
        "client_app": app.name,
        "salt": random.randint(0, 700),
    }
    print("JWT is set to expire in", request.expires_in, request.claims)
    return common.generate_signed_token(settings.OAUTH2_JWT["PRIVATE_KEY"], request)


class JWTServer(openid.Server):
    def __init__(
        self,
        request_validator,
        token_expires_in=None,
        token_generator=None,
        *args,
        **kwargs
    ):
        token_generator = custom_token_generator
        super().__init__(
            request_validator, token_expires_in, token_generator, *args, **kwargs
        )
