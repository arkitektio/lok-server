from oauth2_provider.oauth2_validators import OAuth2Validator



class JWTValidator(OAuth2Validator):

    def validate_client_id(self, client_id, request, *args, **kwargs):
        validated =  super().validate_client_id(client_id, request, *args, **kwargs)
        if not validated: return False
        request.claims = {
        'aud': client_id
        }
        return True


