from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
import django_filters


class UserFilter(django_filters.FilterSet):
    class Meta:
        model = get_user_model()
        fields = ["username", "email"]


class GroupFilter(django_filters.FilterSet):
    class Meta:
        model = Group
        fields = ["name"]
