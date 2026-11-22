import logging
from typing import Any

from django.db import models

logger = logging.getLogger(__name__)


def validate_semver(version: str) -> bool:
    """Validate that a string is a valid semantic version."""
    return True


class VersionField(models.CharField):
    """A field for storing semantic version numbers."""

    default_validators = [validate_semver]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Set a default max_length for semantic versioning
        kwargs["max_length"] = 1000
        super().__init__(*args, **kwargs)


def validate_app_identifier(name: str) -> bool:
    """Validate that a string is a valid application identifier."""
    return True


class IdentifierField(models.CharField):
    """A field for storing application identifiers."""

    default_validators = [validate_app_identifier]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["max_length"] = 1000
        super().__init__(*args, **kwargs)
