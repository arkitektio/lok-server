from balder.settings import get_active_settings
import graphene
from graphene_django.converter import convert_django_field
from django.db import models
from graphene.types.generic import GenericScalar

@convert_django_field.register(models.JSONField)
def convert_json_field_to_string(field, registry=None):
    return GenericScalar()

settings = get_active_settings()

class BalderRegistry:

    def __init__(self) -> None:
        
        self.queries = {"void": graphene.String(default_value="Hallo")}
        self.mutations = {}
        self.subscriptions = {}
        self.types = []

    def registerType(self, type):
        self.types.append(type)


    def registerQuery(self, query):
        self.queries[query._get_operation()] = query._to_field()

    def registerMutation(self, mutation):
        self.mutations[mutation._get_operation()] = mutation._to_field()

    def registerSubscription(self, subscription):
        self.subscriptions[subscription._get_operation()] = subscription._to_field()
    

    def buildSchema(self, query=None, mutation=None, subscription=None, types=[]):
        assert issubclass(query, graphene.ObjectType) or query is None, "If you provide an additional root Query please make sure its of type Query"
        QueryBase = query if query is not None else graphene.ObjectType
        MutationBase = mutation if mutation is not None else graphene.ObjectType
        SubscriptionBase = subscription if subscription is not None else graphene.ObjectType

        query = type("Query", (QueryBase, ), {**self.queries, "__doc__": "The root Query"}) if self.queries != {} else None
        mutation = type("Mutation", (MutationBase, ), {**self.mutations, "__doc__": "The root Mutation"}) if self.mutations != {} else None

        if settings.SUBSCRIPTIONS:
            subscription = type("Subscription", (SubscriptionBase, ), {**self.subscriptions, "__doc__": "The root Subscriptions"}) if self.subscriptions != {} else None
        else:
            subscription = None

        return graphene.Schema(
            query = query,
            mutation = mutation,
            subscription = subscription,
            types = self.types + types
        )









BALDER_REGISTRY = None

def get_balder_registry():
    global BALDER_REGISTRY
    if BALDER_REGISTRY is None:
        BALDER_REGISTRY = BalderRegistry()
    return BALDER_REGISTRY


def register_type(cls):
    registry = get_balder_registry()
    if cls not in registry.types:
        registry.registerType(cls)
    return cls