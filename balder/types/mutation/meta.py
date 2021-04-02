from abc import ABCMeta
from balder.registry import get_balder_registry
import logging

logger = logging.getLogger(__name__)

class BalderDefaultMutationMeta:
    abstract = False
    personal = None
    list = None
    filter = None
    operation = None
    queryset = None

class EmptyOverrideMeta:
    pass

class EmptyArguments:
    pass


class BalderMutationMeta(ABCMeta):

    def __new__(cls, name, bases, ns, **kwargs):

        return super(ABCMeta, cls).__new__(cls, name, bases, ns, **kwargs)

    def __init__(cls, name, bases, ns, **kwargs):
        super(ABCMeta, cls).__init__(name, bases, ns)
        meta_overrides = getattr(cls, "Meta", EmptyOverrideMeta)
        cls._meta = type("OverwrittenMeta", (meta_overrides, BalderDefaultMutationMeta), {"__doc__": f"Overwritten Meta for {cls.__name__}"})
        cls._arguments = getattr(cls, "Arguments", EmptyArguments)
        if not cls._meta.abstract:
            logger.info(f"Registering {name} as Mutation")
            get_balder_registry().registerMutation(cls)