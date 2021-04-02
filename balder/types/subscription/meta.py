from abc import ABCMeta
from balder.types.subscription.serializer import PayloadSerializer
from balder.registry import get_balder_registry
import logging

logger = logging.getLogger(__name__)

class BalderDefaultSubscriptionMeta:
    abstract = False
    operation = None
    serializer = PayloadSerializer
    model = None

class EmptyOverrideMeta:
    pass

class EmptyArguments:
    pass


class BalderSubscriptionMeta(ABCMeta):

    def __new__(cls, name, bases, ns, **kwargs):

        return super(ABCMeta, cls).__new__(cls, name, bases, ns, **kwargs)

    def __init__(cls, name, bases, ns, **kwargs):
        super(ABCMeta, cls).__init__(name, bases, ns)
        meta_overrides = getattr(cls, "Meta", EmptyOverrideMeta)
        cls._meta = type("OverwrittenMeta", (meta_overrides, BalderDefaultSubscriptionMeta), {"__doc__": f"Overwritten Meta for {cls.__name__}"})
        cls._arguments = getattr(cls, "Arguments", EmptyArguments)
        if not cls._meta.abstract:
            logger.info(f"Registering {name} as Subscription")
            get_balder_registry().registerSubscription(cls)