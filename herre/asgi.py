"""
ASGI config for herre project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/3.1/howto/deployment/asgi/
"""

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.conf.urls import url

from django.core.asgi import get_asgi_application
from balder.consumers import MyGraphqlWsConsumer


# The channel routing defines what connections get handled by what consumers,
# selecting on either the connection type (ProtocolTypeRouter) or properties
# of the connection's scope (like URLRouter, which looks at scope["path"])
# For more, see http://channels.readthedocs.io/en/latest/topics/routing.html
MiddleWareStack = lambda inner: AuthMiddlewareStack(inner)


application = ProtocolTypeRouter(
    {
        # Channels will do this for you automatically. It's included here as an example.
        "http": get_asgi_application(),
        # Route all WebSocket requests to our custom chat handler.
        # We actually don't need the URLRouter here, but we've put it in for
        # illustration. Also note the inclusion of the AuthMiddlewareStack to
        # add users and sessions - see http://channels.readthedocs.io/en/latest/topics/authentication.html
        "websocket": MiddleWareStack(
            URLRouter(
                [
                    url("graphql/", MyGraphqlWsConsumer.as_asgi()),
                    url("graphql", MyGraphqlWsConsumer.as_asgi()),
                ]
            )
        ),
    }
)
