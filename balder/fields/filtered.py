from functools import partial
from graphene import Field, List
from graphene_django.filter.utils import (get_filtering_args_from_filterset,
                                          get_filterset_class)


class BalderFiltered(Field):
    """[summary]

    Args:
        Field ([type]): [description]
    """

    def __init__(self, _type, related_field=None, fields=None, extra_filter_meta=None,
                 filterset_class=None, queryset_resolver=None, *args, **kwargs):
        """A Filtered field for any Query

        Args:
            _type ([type]): [description]
            related_field ([type], optional): The related field that we are using the manager from. Defaults to None.
            fields ([type], optional): [description]. Defaults to None.
            extra_filter_meta ([type], optional): [description]. Defaults to None.
            filterset_class ([type], optional): [description]. Defaults to None.
            queryset_resolver ([type], optional): Function that resolves the initial queryset for the filter, is overwritten by related_field. Default to None
        """
        _fields = _type._meta.filter_fields
        _model = _type._meta.model
        standard_resolver = lambda root, info, *args, **kwargs: _model._default_manager.get_queryset()

        self._relatedfield = related_field
        self._queryset_resolver = queryset_resolver or standard_resolver
        self.of_type = _type


        self.fields = fields or _fields
        meta = dict(model=_model, fields=self.fields)
        if extra_filter_meta:
            meta.update(extra_filter_meta)
        self.filterset_class = get_filterset_class(filterset_class, **meta) # create one if there is no filtersetclass provided
        self.filtering_args = get_filtering_args_from_filterset(
            self.filterset_class, _type)
        kwargs.setdefault('args', {})
        kwargs['args'].update(self.filtering_args)
        super().__init__(List(_type), *args, **kwargs)

    @staticmethod
    def related_resolver(related_field, filterset_class, filtering_args, root, info, *args, **kwargs):
        #TODO: here it would be good to filter for permissions if it is ndeeded
        
        filter_kwargs = {k: v for k,
                         v in kwargs.items() if k in filtering_args}
        qs = getattr(root, related_field)
        qs = filterset_class(data=filter_kwargs, queryset=qs).qs
        return qs


    @staticmethod
    def list_resolver(queryset_resolver, filterset_class, filtering_args, root, info, *args, **kwargs):
        #TODO: here it would be good to filter for permissions if it is ndeeded
        
        filter_kwargs = {k: v for k,
                         v in kwargs.items() if k in filtering_args}
        qs = filterset_class(data=filter_kwargs, queryset=queryset_resolver(root, info, *args, **kwargs)).qs
        return qs

    def get_resolver(self, parent_resolver):
        if self._relatedfield:
            # if related field is set we are dealing with a related field manager
            # so we will get the manager from the root object (through accessing the related field)
            return partial(self.related_resolver, self._relatedfield,
                        self.filterset_class, self.filtering_args)
        else:
            # We are just using the default manager of this class...
            # TODO: Maybe warn if we are doing this from a related field
            return partial(self.list_resolver, self._queryset_resolver,
                       self.filterset_class, self.filtering_args)