from enum import Enum
import graphene

class GrantType(str, graphene.Enum):
    CLIENT_CREDENTIALS = "client-credentials"
    IMPLICIT = "implicit"
    PASSWORD = "password"
    AUTHORIZATION_CODE = "authorization-code"