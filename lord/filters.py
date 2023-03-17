from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
import django_filters
import graphene
from django import forms
from graphene_django.forms.converter import convert_form_field


class IDChoiceField(forms.JSONField):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

    def overwritten_type(self, **kwargs):
        return graphene.List(graphene.ID, **kwargs)


@convert_form_field.register(IDChoiceField)
def convert_form_field_to_string_list(field):
    return field.overwritten_type(required=field.required)


class IDChoiceFilter(django_filters.MultipleChoiceFilter):
    field_class = IDChoiceField

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, field_name="pk")


class IdsFilter(django_filters.FilterSet):

    ids = IDChoiceFilter(label="Filter by values")

    def my_values_filter(self, queryset, name, value):
        if value:
            return queryset.filter(id__in=value)
        else:
            return queryset

class UserFilter(IdsFilter, django_filters.FilterSet):
    search = django_filters.CharFilter(
        field_name="username", lookup_expr="icontains", label="Search for substring of username"
    )



    class Meta:
        model = get_user_model()
        fields = ["username", "email"]


class GroupFilter(IdsFilter, django_filters.FilterSet):
    search = django_filters.CharFilter(
        field_name="name", lookup_expr="icontains", label="Search for substring of username"
    )

    class Meta:
        model = Group
        fields = ["name"]
