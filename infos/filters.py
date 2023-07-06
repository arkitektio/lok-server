import django_filters
from balder.filters import EnumFilter, MultiEnumFilter
from . import models
from .enums import FilterMethod
class AppFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(
        field_name="identifier", lookup_expr="icontains", label="Search for substring of identifier"
    )


class ReleaseFilter(django_filters.FilterSet):
    app = django_filters.ModelChoiceFilter(
        field_name="app", queryset=models.App.objects.all(), label="Filter by app"
    )


class ConfigurationFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(
        field_name="name", lookup_expr="icontains", label="Search for substring of identifier"
    )

class LinkerFilter(django_filters.FilterSet):
    template = django_filters.ModelChoiceFilter(
        field_name="template", queryset=models.ConfigurationTemplate.objects.all(), label="Filter by template"
    )
    search = django_filters.CharFilter(
        field_name="name", lookup_expr="icontains", label="Search for substring of identifier"
    )

class FilterFilter(django_filters.FilterSet):
    linker = django_filters.ModelChoiceFilter(
        field_name="linker", queryset=models.ConfigurationTemplate.objects.all(), label="Filter by linker"
    )
    methods = MultiEnumFilter(type=FilterMethod,
        field_name="name", label="Search for methods of identifier"
    )