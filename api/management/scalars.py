from typing import Any, NewType

import strawberry

ExtraData = NewType("ExtraData", object)


scalar_map: dict[Any, Any] = {
    ExtraData: strawberry.scalar(
        name="ExtraData",
        description="The `ArrayLike` scalasr typsse represents a reference to a store previously created by the user n a datalayer",
        serialize=lambda v: v,
        parse_value=lambda v: v,
    ),
}
