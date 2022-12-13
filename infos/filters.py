import django_filters

class AppFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(
        field_name="identifier", lookup_expr="icontains", label="Search for substring of identifier"
    )