import strawberry
from enum import Enum
from django.db.models import Model, QuerySet
from typing import Any, Callable, Optional, Type, Dict, Tuple, List
from django.db.models import Avg, Max, Min, Sum, Count, DateField, FloatField
from django.db.models.functions import Extract, TruncHour, TruncDay, TruncWeek, TruncMonth, TruncQuarter, TruncYear
import strawberry_django
from kante import Info
import datetime

# ---------- Resolver spec ----------
ResolverSpec = Dict[str, Tuple[Callable[[QuerySet, str], Any], Type, str]]


# --------- Bucket return type ---------
@strawberry.type
class TimeBucket:
    ts: datetime.datetime  # bucket timestamp
    count: int
    distinctCount: int
    max: Optional[float]
    min: Optional[float]
    avg: Optional[float]
    sum: Optional[float]


@strawberry.enum
class Granularity(str, Enum):
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


def create_stats_type(
    model: Type[Model],
    *,
    filters: Optional[Type[Any]] = None,  # strawberry_django filter class for the model
    allowed_fields: Dict[str, str] | None = None,  # GraphQL name -> model field (string)
    allowed_datetime_fields: Dict[str, str] | None = None,  # GraphQL name -> DateTimeField (string) for bucketing
    resolvers: ResolverSpec | None = None,
    type_name: Optional[str] = None,
    enum_name: Optional[str] = None,
    dt_enum_name: Optional[str] = None,
    prescope: Optional[Callable[[QuerySet, Info], QuerySet]] = None,
) -> Tuple[Type[Any], Callable[..., Any]]:
    """
    Build a Strawberry GraphQL Stats type for `model`.

    - FieldEnum: which numeric-like field to aggregate on.
    - DateTimeFieldEnum: which datetime to bucket by.
    - Scalar stats (count/distinctCount/max/min/avg/sum) each accept `field: FieldEnum`,
      but under the hood we aggregate ALL of them in ONE query per `field` and memoize.
    - series(field:, timestampField:, by:) returns time buckets with per-bucket stats
      via ONE query (GROUP BY truncated timestamp).
    """

    filters_type = filters
    if allowed_fields is None:
        raise ValueError("allowed_fields must be provided")

    # --------- Enums ---------
    enum_name = enum_name or f"{model.__name__}Field"
    FieldEnumPy = Enum(enum_name, {k.upper(): v for k, v in allowed_fields.items()})
    FieldEnum = strawberry.enum(
        FieldEnumPy,
        description=f"Numeric/aggregatable fields of {model.__name__}",
    )  # type: ignore

    if allowed_datetime_fields:
        dt_enum_name = dt_enum_name or f"{model.__name__}TimestampField"
        TimestampEnumPy = Enum(dt_enum_name, {k.upper(): v for k, v in allowed_datetime_fields.items()})
        TimestampEnum = strawberry.enum(TimestampEnumPy, description=f"Datetime fields of {model.__name__} for bucketing")
    else:
        TimestampEnumPy = None
        TimestampEnum = None  # type: ignore

    # --------- Helpers ---------
    def _truncate(dt_field: str, by: Granularity) -> Any:
        return {
            "hour": TruncHour(dt_field),
            "day": TruncDay(dt_field),
            "week": TruncWeek(dt_field),
            "month": TruncMonth(dt_field),
            "quarter": TruncQuarter(dt_field),
            "year": TruncYear(dt_field),
        }[by.value]

    def _is_temporal(mf: str) -> bool:
        """True if `mf` (may span relations) resolves to a date/datetime field."""
        opts = model._meta
        field = None
        for part in mf.split("__"):
            field = opts.pk if part == "pk" else opts.get_field(part)
            if field.is_relation and field.related_model is not None:
                opts = field.related_model._meta
        return isinstance(field, DateField)  # DateTimeField subclasses DateField

    def _stat_aggregates(mf: str) -> Dict[str, Any]:
        """
        The max/min/avg/sum aggregates for the model field.

        Postgres has no avg()/sum() over timestamps, and the stats are exposed as floats,
        so temporal fields are aggregated as epoch seconds (their sum is meaningless and left out).
        """
        if _is_temporal(mf):
            epoch = Extract(mf, "epoch", output_field=FloatField())
            return dict(max=Max(epoch), min=Min(epoch), avg=Avg(epoch))
        return dict(max=Max(mf), min=Min(mf), avg=Avg(mf), sum=Sum(mf))

    # Default scalar resolver functions (unused directly now; we aggregate once)
    def _all_stats_for_field(qs: QuerySet, mf: str) -> Dict[str, Any]:
        """
        Return all scalar stats for the model field in ONE aggregate.
        """
        summary = qs.aggregate(
            distinctCount=Count(mf, distinct=True),
            **_stat_aggregates(mf),
            # Note: 'count' shown separately as total rows, not non-null count.
            # If you prefer non-null count for the field, use Count(mf) here.
        )
        summary.setdefault("sum", None)
        return summary

    # Build resolver spec but each field will read from the same cached aggregate:
    default_resolvers: ResolverSpec = {
        "distinctCount": (
            lambda qs, mf: _all_stats_for_field(qs, mf)["distinctCount"],
            int,
            "Number of distinct values for the field",
        ),
        "max": (lambda qs, mf: _all_stats_for_field(qs, mf)["max"], Optional[float], "Maximum"),
        "min": (lambda qs, mf: _all_stats_for_field(qs, mf)["min"], Optional[float], "Minimum"),
        "avg": (lambda qs, mf: _all_stats_for_field(qs, mf)["avg"], Optional[float], "Average"),
        "sum": (lambda qs, mf: _all_stats_for_field(qs, mf)["sum"], Optional[float], "Sum"),
    }
    resolvers = {**default_resolvers, **(resolvers or {})}

    # --------- Class body with memoized aggregates ---------
    cls_name = type_name or f"{model.__name__}Stats"
    body: Dict[str, Any] = {
        "__annotations__": {
            "_qs": strawberry.Private[QuerySet],
            "_cache": strawberry.Private[Dict[str, Dict[str, Any]]],  # per-field aggregate cache
            "count": int,
        },
        "__init__": lambda self, **data: setattr(self, "__dict__", data),
        "_qs": None,  # type: ignore
        "_cache": None,  # type: ignore
        "count": strawberry_django.field(
            description="Total number of items in the selection",
            resolver=lambda self: self._qs.count(),
        ),
        "_get_field_summary": lambda self, mf: self._cache.setdefault(
            mf,
            _all_stats_for_field(self._qs, mf),
        ),
    }

    # Attach scalar stat fields that share the same cached aggregate
    for stat_name, (_fn, return_type, desc) in resolvers.items():

        def _make_resolver(stat_key: str):
            def _resolver(self, field: FieldEnumPy):
                mf: str = field.value
                summary = self._get_field_summary(mf)
                if stat_key == "distinctCount":
                    return summary["distinctCount"]
                elif stat_key in ("max", "min", "avg", "sum"):
                    return summary[stat_key]
                else:
                    # fallback: compute via function if someone extended `resolvers`
                    return resolvers[stat_key][0](self._qs, mf)

            _resolver.__name__ = stat_name
            return _resolver

        body["__annotations__"][stat_name] = return_type
        body[stat_name] = strawberry_django.field(description=desc, resolver=_make_resolver(stat_name))

    # Optional: time-bucketed series
    if TimestampEnum is not None:

        def _series(self, field: FieldEnumPy, timestampField: TimestampEnumPy, by: Granularity) -> List[TimeBucket]:
            mf = field.value
            tf = timestampField.value
            trunc = _truncate(tf, by)

            # ONE query with GROUP BY bucket
            qs = (
                self._qs.annotate(bucket=trunc)
                .values("bucket")
                .annotate(
                    count=Count("pk"),
                    distinctCount=Count(mf, distinct=True),
                    **_stat_aggregates(mf),
                )
                .order_by("bucket")
            )

            out: List[TimeBucket] = []
            for row in qs:
                out.append(
                    TimeBucket(
                        ts=row["bucket"],
                        count=row["count"],
                        distinctCount=row["distinctCount"],
                        max=row["max"],
                        min=row["min"],
                        avg=row["avg"],
                        sum=row.get("sum"),
                    )
                )
            return out

        body["__annotations__"]["series"] = List[TimeBucket]
        body["series"] = strawberry_django.field(
            description="Time-bucketed stats over a datetime field.",
            resolver=_series,
        )

    # Create the Python class and wrap with strawberry
    new_type = strawberry.type(type(cls_name, (), body))

    # --------- Root field resolver (fixed filter handling + cache init) ---------
    def resolver(info: Info, filters: Optional[Any] = None) -> Any:
        qs = model.objects

        if prescope is not None:
            qs = prescope(qs, info)
        else:
            qs = qs.all()

        if filters_type and filters is not None:
            # `filters` is an instance or dict per strawberry_django usage
            qs = strawberry_django.filters.apply(filters, qs, info)
        # instance with caches
        return new_type(_qs=qs, _cache={})

    # Expose argument types nicely on the callable (optional)
    if filters_type is not None:
        resolver.__annotations__["filters"] = Optional[filters_type]  # type: ignore

    return new_type, resolver
