from balder.types.query import BalderQuery
from balder.registry import get_balder_registry
import graphene
from balder.autodiscover import autodiscover

class Query(graphene.ObjectType):
    hello = graphene.String(default_value="Hi!")

    def resolve_hello(*args, **kwargs):
        return "Hallo"


# Autodiscover for all of the Balder Modules in the installed Apps

autodiscover()

graphql_schema = get_balder_registry().buildSchema(query = Query)