import strawberry
import strawberry_django
from fakts import models


@strawberry_django.order_type(models.Client)
class OAuth2ClientOrdering:
    id: strawberry.auto


@strawberry_django.type(models.Client, ordering=OAuth2ClientOrdering)
class Oauth2Client:
    id: str
    client_id: str
    
    
    