from balder.settings import get_active_settings
from typing import List, Union
from django.db.models import Model
from pydantic.main import BaseModel
from balder.types.subscription.meta import BalderSubscriptionMeta
from balder.types.utils import classToString
import graphene

settings = get_active_settings()

if settings.SUBSCRIPTIONS: 
    import channels_graphql_ws

    class BaseSubscription(channels_graphql_ws.Subscription):

        @classmethod
        def subscribe(cls, root, info, *args, **kwargs):
            raise NotImplementedError("Should never be called directly")

        @classmethod
        def publish(cls, payload, info, *args, **kwargs):
            raise NotImplementedError("Should never be called directly")

else:
    BaseSubscription = None
    

class IllConfigured(Exception):
    pass

class NoMutateException(Exception):
    pass


class BalderSubscription(metaclass=BalderSubscriptionMeta):
    Arguments = None # Should be overwriten by the actual mutation


    class Meta:
        abstract = True


    @classmethod
    def _get_description(cls) -> str:
        return cls.__doc__

    @classmethod
    def _get_publish_or_none(cls) -> str:
        if hasattr(cls, "publish"):
            return cls.publish
        else:
            return None

    @classmethod
    def _get_subscribe_or_none(cls) -> str:
        if hasattr(cls, "subscribe"):
            return cls.subscribe
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
    def broadcast(cls, payload: Union[dict, str, Model, BaseModel], groups: List[str]):
        if cls._meta.serializer is not None:
            payload = cls._meta.serializer.pack(payload)
        for group in groups:
            cls.subscription.broadcast(payload=payload, group=group)



    @classmethod
    def _to_field(cls) -> graphene.Field:
        meta = cls._meta
        assert meta.type is not None, f"Please provide a Meta class with at least the attribute type in {cls.__name__}"
        assert cls.Arguments is not None, f'Please provide a Arguments class (can just pass in body if no arguments are needed) in  {cls.__name__}'

        subscribe_function = cls._get_subscribe_or_none()
        assert subscribe_function is not None, f"Please provide a subscribe function in  {cls.__name__}"


        publish_function = cls._get_publish_or_none()
        if publish_function is None:
            assert meta.serializer is not None, "If you omit publish please provide a Meta field serializer in {cls.__name__}"
            publish_function =  lambda payload, info, *args, **kwargs: meta.serializer.unpack(payload, basemodel=meta.model)

        assert BaseSubscription is not None, "You have not installed django-grahql-ws so subscriptions are not enabled!"
        cls.subscription = type("Subscription", (BaseSubscription,), {"Arguments": cls.Arguments, "Output": meta.type, "subscribe": subscribe_function, "publish": publish_function, "__doc__": cls._get_description()})

        return cls.subscription.Field(description=cls._get_description())




    