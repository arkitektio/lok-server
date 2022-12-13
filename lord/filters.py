from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
import django_filters


class UserFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(
        field_name="username", lookup_expr="icontains", label="Search for substring of username"
    )



    class Meta:
        model = get_user_model()
        fields = ["username", "email"]


class GroupFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(
        field_name="name", lookup_expr="icontains", label="Search for substring of username"
    )

    class Meta:
        model = Group
        fields = ["name"]
