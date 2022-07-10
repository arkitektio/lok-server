from oauth2_provider.oauth2_validators import OAuth2Validator
import logging


class JWTValidator(OAuth2Validator):
    def validate_client_id(self, client_id, request, *args, **kwargs):
        validated = super().validate_client_id(client_id, request, *args, **kwargs)
        if not validated:
            return False
        request.claims = {"aud": client_id}
        return True


class CustomOAuth2Validator(OAuth2Validator):
    # Set `oidc_claim_scope = None` to ignore scopes that limit which claims to return,
    # otherwise the OIDC standard scopes are used.

    def get_additional_claims(self, request):
        logging.error("INASODINAOSDINAOSINDOASND")
        return {
            "given_name": request.user.first_name,
            "family_name": request.user.last_name,
            "name": " ".join([request.user.first_name, request.user.last_name]),
            "preferred_username": request.user.username,
            "email": request.user.email,
            "roles": [group.name for group in request.user.groups.all()],
        }

    def get_userinfo_claims(self, request):
        claims = super().get_userinfo_claims(request)
        logging.error("ffff")
        return {**claims, **self.get_additional_claims(request)}
