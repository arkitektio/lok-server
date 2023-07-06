import graphene
from .enums import FilterMethod


class FilterInput(graphene.InputObjectType):
    method = graphene.Argument(FilterMethod, required=True)
    value = graphene.String(required=True)