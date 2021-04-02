from enum import Enum
from typing import Type
from django.db.models.enums import TextChoices
import graphene

class InputEnum:

    @staticmethod
    def from_choices(enum_class: Type[TextChoices], description=None):
        description = description or enum_class.__doc__

        def des(v):
            return enum_class[v.value].label if v is not None else description

        return graphene.Enum(f"{enum_class.__name__}Input", [(tag.name, tag.name) for tag in enum_class], description= des)

