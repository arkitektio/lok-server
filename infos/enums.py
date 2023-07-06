import graphene
from infos.models import Filter


FilterMethod = type(
    "FilterMethod",
    (graphene.Enum,),
    {m.upper(): m for m, description in Filter.FILTER_CHOICES},
)
