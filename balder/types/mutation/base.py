from abc import ABC, abstractmethod
from balder.fields.filtered import BalderFiltered
from balder.types.mutation.meta import BalderMutationMeta
from balder.types.utils import classToString
import graphene
import django_filters
from graphene_django import DjangoObjectType

class IllConfigured(Exception):
    pass

class NoMutateException(Exception):
    pass



class BalderMutation(metaclass=BalderMutationMeta):
    Arguments = None # Should be overwriten by the actual mutation


    class Meta:
        abstract = True


    @classmethod
    def _get_description(cls) -> str:
        return cls.__doc__

    @classmethod
    def _get_mutate_or_none(cls) -> str:
        if hasattr(cls, "mutate"):
            return cls.mutate
        else:
            return None

    @classmethod
    def _get_operation(cls) -> str:
        """Gets the opereration name (string literal in query)

        Returns:
            str: the operation name
        """
        if cls._meta.operation is not None: return cls._meta.operation
        return classToString(cls)

    @classmethod
    def _to_field(cls) -> graphene.Field:
        meta = cls._meta
        assert meta.type is not None, f"Please provide a Meta class with at least the attribute type in {cls.__name__}"
        assert cls.Arguments is not None, f'Please provide a Arguments class (can just pass in body if no arguments are needed) in  {cls.__name__}'

        mutate_function = cls._get_mutate_or_none()
        assert mutate_function is not None, f"Please provide a mutate function in  {cls.__name__}"

        mutation = type("Mutation", (graphene.Mutation,), {"Arguments": cls.Arguments, "Output": meta.type, "mutate": mutate_function, "__doc__": cls._get_description()})
        return mutation.Field(description=cls._get_description())




    