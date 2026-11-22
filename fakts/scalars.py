from typing import Any, NewType

import strawberry

AppIdentifier = NewType("AppIdentifier", str)
Version = NewType("Version", str)
ServiceIdentifier = NewType("ServiceIdentifier", str)
Fakt = NewType("Fakt", object)


scalar_map: dict[Any, Any] = {
    AppIdentifier: strawberry.scalar(
        name="AppIdentifier",
        description="The App identifier is a unique identifier for an app. It is used to identify the app in the database and in the code. We encourage you to use the reverse domain name notation. E.g. `com.example.myapp`",
        serialize=lambda v: v,
        parse_value=lambda v: v,
    ),
    Version: strawberry.scalar(
        name="Version",
        description="The `Version` represents a semver version string",
        serialize=lambda v: v,
        parse_value=lambda v: v,
    ),
    ServiceIdentifier: strawberry.scalar(
        name="ServiceIdentifier",
        description="The Service identifier is a unique identifier for a service. It is used to identify the service in the database and in the code. We encourage you to use the reverse domain name notation. E.g. `com.example.myservice`",
        serialize=lambda v: v,
        parse_value=lambda v: v,
    ),
    Fakt: strawberry.scalar(
        name="Fakt",
        description="The `Fakt` scalar type represents a reference to a fakt",
        serialize=lambda v: v,
        parse_value=lambda v: v,
    ),
}
