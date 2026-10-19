from typing import Any, NewType
import strawberry

ExtraData = NewType("ExtraData", object)


scalar_map: dict[Any, Any] = {
    ExtraData: strawberry.scalar(
        name="ExtraData",
        description="Just a placeholder for extra data that can be attached to a model instance.",
        serialize=lambda v: v,
        parse_value=lambda v: v,
    ),
}
