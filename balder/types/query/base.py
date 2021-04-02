from abc import ABC, abstractmethod
from balder.fields.filtered import BalderFiltered
from balder.types.query.meta import BalderQueryMeta
from balder.types.utils import classToString
import graphene
import django_filters
from graphene_django import DjangoObjectType
from graphene.utils.props import props


class IllConfigured(Exception):
    pass

class NoResolveError(Exception):
    pass



class BalderQuery(metaclass=BalderQueryMeta):

    class Meta:
        abstract = True
        personal = None
        list = None
        filter = None
        operation = None


    @classmethod
    def cls_resolve(cls, root, info, *args, **kwargs):
        try:
            return cls.resolve(root, info, *args, **kwargs)
        except NoResolveError as e:
            raise NoResolveError(f"Please overwride resolve in {cls.__name__} at {cls.__module__}")

    @classmethod
    def _get_description(cls) -> str:
        return cls.__doc__

    @classmethod
    def _get_resolver_or_none(cls) -> str:
        if hasattr(cls, "resolve"):
            return cls.resolve
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

        resolver = cls._get_resolver_or_none()
        if resolver:
            # Already provided resolver overwrites every default
            assert cls._arguments is not None, f"If you are using a custom resolver please also provide an Arguments class (can pass) in {cls.__name__}"
            attributes = props(cls._arguments)
            if meta.list:
                return graphene.List(meta.type, resolver=resolver, description=cls._get_description(), **attributes)
            else:
                return graphene.Field(meta.type, resolver=resolver, description=cls._get_description(), **attributes)

        assert issubclass(meta.type, DjangoObjectType), "You cannot use Query without a custom resolver if it is not a DjangoObjectType"
        qs = meta.queryset or meta.type._meta.model._default_manager.get_queryset()

        if meta.personal:
            # Personal means we want to filter the queryset for the user with that fied
            assert isinstance(meta.personal, str), "Meta attribute personal must be of type str"
            list_resolver = lambda root, info, *args, **kwargs: qs.filter(**{meta.personal: info.context.user})
            item_resolver = lambda root, info, *args, **kwargs: qs.filter(**{meta.personal: info.context.user}).first()
        else:
            list_resolver = lambda root, info, *args, **kwargs: qs
            item_resolver = lambda root, info, *args, **kwargs: qs.first()

        if meta.filter:
            if issubclass(meta.filter, django_filters.FilterSet):
                return BalderFiltered(meta.type,  description=cls._get_description(), filterset_class=meta.filter, queryset_resolver=list_resolver)
            else:
                raise IllConfigured("Meta attribute filter must be of type django_filters.FilterSet")
        
        if meta.list:
            return graphene.List(meta.type, resolver=list_resolver, description=cls._get_description())
        else:
            return graphene.Field(meta.type, resolver=item_resolver, description=cls._get_description())



    