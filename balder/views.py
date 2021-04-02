
from balder.settings import get_active_settings
from graphene_django.views import GraphQLView
from django.views.decorators.csrf import csrf_exempt

settings = get_active_settings()

if settings.SUBSCRIPTIONS: 
    import channels_graphql_ws
    GraphQLView.graphiql_template = "graphene/graphiql-ws.html"


BalderView = csrf_exempt(GraphQLView.as_view(graphiql=True))